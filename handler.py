"""RunPod serverless handler for ComfyUI.

Lean import surface at module load (just runpod + stdlib) so the worker is
immediately responsive to RunPod health checks. Heavy deps (requests, PIL,
websocket-client) and ComfyUI itself are loaded/started lazily on the first
job that actually needs them.

Job input schema:
{
  "input": {
    "workflow": { ... ComfyUI API-format workflow JSON ... },
    "images":   [ {"name": "ref.png", "image": "<base64>"} ],   # optional
    "return":   "base64" | "url"                                 # default base64
    "diagnostic": true                                           # return worker state
  }
}
"""

import os
import socket
import subprocess
import threading

import runpod

COMFY_PORT = os.environ.get("COMFY_PORT", "8188")
COMFY_HTTP = f"http://127.0.0.1:{COMFY_PORT}"
COMFY_WS = f"ws://127.0.0.1:{COMFY_PORT}/ws"
JOB_TIMEOUT_S = int(os.environ.get("JOB_TIMEOUT_S", "600"))
COMFY_STARTUP_TIMEOUT = int(os.environ.get("COMFY_STARTUP_TIMEOUT", "300"))

# Lazy ComfyUI startup state
_comfy_lock = threading.Lock()
_comfy_proc = None  # type: ignore


def _start_comfy_if_needed():
    """Spawn ComfyUI as a child process on first call. Idempotent + thread-safe."""
    global _comfy_proc
    with _comfy_lock:
        if _comfy_proc is not None and _comfy_proc.poll() is None:
            return  # already running

        # Wire input/output/custom_nodes to volume if present
        if os.path.isdir("/runpod-volume"):
            for d in ("input", "output", "custom_nodes"):
                os.makedirs(f"/runpod-volume/{d}", exist_ok=True)
                target = f"/ComfyUI/{d}"
                try:
                    if os.path.islink(target):
                        os.unlink(target)
                    elif os.path.exists(target):
                        import shutil
                        shutil.rmtree(target, ignore_errors=True)
                except Exception:
                    pass
                try:
                    os.symlink(f"/runpod-volume/{d}", target)
                except FileExistsError:
                    pass

            # Install pip deps for custom nodes (idempotent — pip skips already-installed)
            import glob
            for req in glob.glob("/runpod-volume/custom_nodes/*/requirements.txt"):
                print(f"[handler] pip install -r {req}", flush=True)
                subprocess.run(
                    ["pip", "install", "--no-cache-dir", "-q", "-r", req],
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.STDOUT,
                )

        log = open("/tmp/comfyui.log", "ab")
        print(f"[handler] launching ComfyUI on :{COMFY_PORT}", flush=True)
        _comfy_proc = subprocess.Popen(
            ["python", "-u", "/ComfyUI/main.py",
             "--listen", "127.0.0.1",
             "--port", COMFY_PORT,
             "--disable-auto-launch",
             "--disable-metadata"],
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )


def _wait_for_comfy(timeout_s=COMFY_STARTUP_TIMEOUT):
    import time
    import requests
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            r = requests.get(f"{COMFY_HTTP}/system_stats", timeout=2)
            if r.ok:
                return
        except requests.RequestException:
            pass
        time.sleep(0.5)
    # Surface ComfyUI's log to the job response
    tail = ""
    try:
        with open("/tmp/comfyui.log") as f:
            tail = f.read()[-4000:]
    except Exception as e:
        tail = f"(no log: {e})"
    raise RuntimeError(f"ComfyUI did not become ready in time.\nLast log:\n{tail}")


def _diagnostic():
    """Return worker introspection. Only touches the volume if input.deep=True
    so we can isolate whether /runpod-volume mount is the source of slowness."""
    out = {
        "handler_version": "2026-05-22-lazy-v2",
        "hostname": socket.gethostname(),
        "env": {k: os.environ.get(k) for k in ("COMFY_PORT", "COMFY_STARTUP_TIMEOUT", "RUNPOD_POD_ID", "START_MINIMAL")},
        "comfy": {"started": _comfy_proc is not None},
    }
    return out


def _node_diagnostic():
    """Start ComfyUI if not running, then enumerate its registered nodes.
    Surfaces which custom_node imports actually succeeded."""
    _start_comfy_if_needed()
    _wait_for_comfy()
    import requests
    out = {"handler_version": "2026-05-22-node-diag"}
    try:
        r = requests.get(f"{COMFY_HTTP}/object_info", timeout=30)
        info = r.json()
        all_nodes = sorted(info.keys())
        out["total_nodes"] = len(all_nodes)
        out["wanvideo_nodes"] = [n for n in all_nodes if "WanVideo" in n or "Wan" in n]
        out["vhs_nodes"] = [n for n in all_nodes if n.startswith("VHS_") or "Video" in n]
        out["kj_nodes_sample"] = [n for n in all_nodes if any(k in n for k in ("KJ", "ImageResizeKJ", "INTConstant", "GetImageSize"))]
        out["face_mask_present"] = "FaceMaskFromPoseKeypoints" in all_nodes
        out["dwpose_present"] = "DWPreprocessor" in all_nodes
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {e}"
    # Also include the comfy log tail in case there were import errors
    try:
        with open("/tmp/comfyui.log") as f:
            out["log_tail"] = f.read()[-5000:]
    except Exception as e:
        out["log_tail"] = f"(no log: {e})"
    return out


def _deep_diagnostic():
    """Same as _diagnostic but also stats volume + tries ComfyUI."""
    out = _diagnostic()
    out["paths"] = {}
    for p in ("/runpod-volume", "/runpod-volume/models", "/runpod-volume/input",
              "/runpod-volume/custom_nodes", "/ComfyUI", "/ComfyUI/input", "/tmp/comfyui.log"):
        try:
            out["paths"][p] = {"exists": os.path.exists(p), "is_link": os.path.islink(p)}
            if os.path.isdir(p):
                out["paths"][p]["entries"] = sorted(os.listdir(p))[:30]
            elif os.path.islink(p):
                out["paths"][p]["target"] = os.readlink(p)
        except Exception as e:
            out["paths"][p] = {"error": str(e)}
    try:
        import requests
        r = requests.get(f"{COMFY_HTTP}/system_stats", timeout=3)
        out["comfy"]["status"] = f"HTTP {r.status_code}"
        out["comfy"]["body"] = r.json() if r.ok else r.text[:500]
    except Exception as e:
        out["comfy"]["status"] = "unreachable"
        out["comfy"]["error"] = str(e)
    try:
        with open("/tmp/comfyui.log") as f:
            out["comfy"]["log_tail"] = f.read()[-3000:]
    except Exception as e:
        out["comfy"]["log_tail"] = f"(no log: {e})"
    return out


def _upload_images(images):
    import base64
    from io import BytesIO
    import requests
    for item in images:
        name = item["name"]
        blob = base64.b64decode(item["image"])
        files = {"image": (name, BytesIO(blob), "image/png")}
        data = {"overwrite": "true"}
        r = requests.post(f"{COMFY_HTTP}/upload/image", files=files, data=data, timeout=30)
        r.raise_for_status()


def _queue_prompt(workflow, client_id):
    import requests
    r = requests.post(f"{COMFY_HTTP}/prompt", json={"prompt": workflow, "client_id": client_id}, timeout=30)
    if not r.ok:
        raise RuntimeError(f"ComfyUI rejected prompt: {r.status_code} {r.text}")
    return r.json()["prompt_id"]


def _wait_for_completion(prompt_id, client_id):
    import json
    import time
    import websocket
    import requests
    ws = websocket.WebSocket()
    ws.connect(f"{COMFY_WS}?clientId={client_id}", timeout=10)
    deadline = time.time() + JOB_TIMEOUT_S
    try:
        while time.time() < deadline:
            ws.settimeout(min(5.0, deadline - time.time()))
            try:
                msg = ws.recv()
            except websocket.WebSocketTimeoutException:
                continue
            if not isinstance(msg, str):
                continue
            data = json.loads(msg)
            if data.get("type") == "executing":
                d = data.get("data", {})
                if d.get("prompt_id") == prompt_id and d.get("node") is None:
                    break
        else:
            raise TimeoutError(f"Workflow {prompt_id} did not finish in {JOB_TIMEOUT_S}s")
    finally:
        ws.close()
    r = requests.get(f"{COMFY_HTTP}/history/{prompt_id}", timeout=30)
    r.raise_for_status()
    history = r.json().get(prompt_id)
    if not history:
        raise RuntimeError(f"No history for prompt {prompt_id}")
    return history


def _collect_outputs(history, return_mode):
    import base64
    import urllib.parse
    import requests
    outputs = []
    for node_id, node_out in history.get("outputs", {}).items():
        for img in node_out.get("images", []) or []:
            q = urllib.parse.urlencode({"filename": img["filename"],
                                        "subfolder": img.get("subfolder", ""),
                                        "type": img.get("type", "output")})
            data = requests.get(f"{COMFY_HTTP}/view?{q}", timeout=60).content
            outputs.append({"node": node_id, "filename": img["filename"],
                            "image": base64.b64encode(data).decode()})
        for vid in node_out.get("videos", []) or node_out.get("gifs", []) or []:
            q = urllib.parse.urlencode({"filename": vid["filename"],
                                        "subfolder": vid.get("subfolder", ""),
                                        "type": vid.get("type", "output")})
            data = requests.get(f"{COMFY_HTTP}/view?{q}", timeout=180).content
            outputs.append({"node": node_id, "filename": vid["filename"],
                            "data": base64.b64encode(data).decode()})
    return outputs


def handler(job):
    job_input = job.get("input") or {}

    # Diagnostic mode: empty input OR explicit {"diagnostic": true}
    # Empty/diagnostic returns minimal info quickly.
    # {"diagnostic": "deep"} additionally stats the volume + tries ComfyUI.
    if not job_input or job_input.get("diagnostic"):
        if job_input.get("diagnostic") == "nodes":
            return _node_diagnostic()
        if job_input.get("diagnostic") == "deep":
            return _deep_diagnostic()
        return _diagnostic()

    workflow = job_input.get("workflow")
    if not workflow:
        return {"error": "input.workflow is required (ComfyUI API-format JSON)"}

    import uuid
    images = job_input.get("images") or []
    return_mode = job_input.get("return", "base64")

    _start_comfy_if_needed()
    _wait_for_comfy()
    if images:
        _upload_images(images)

    client_id = str(uuid.uuid4())
    prompt_id = _queue_prompt(workflow, client_id)
    history = _wait_for_completion(prompt_id, client_id)
    outputs = _collect_outputs(history, return_mode)
    return {"prompt_id": prompt_id, "images": outputs}


if __name__ == "__main__":
    print("[handler] starting RunPod serverless handler", flush=True)
    runpod.serverless.start({"handler": handler})
