"""RunPod serverless handler for ComfyUI.

Job input schema:
{
  "input": {
    "workflow": { ... ComfyUI API-format workflow JSON ... },
    "images":   [ {"name": "ref.png", "image": "<base64>"} ],   # optional
    "return":   "base64" | "url"                                 # default base64
  }
}
"""

import base64
import json
import os
import time
import urllib.parse
import uuid
from io import BytesIO

import requests
import runpod
import websocket
from PIL import Image

COMFY_HOST = f"127.0.0.1:{os.environ.get('COMFY_PORT', '8188')}"
COMFY_HTTP = f"http://{COMFY_HOST}"
COMFY_WS = f"ws://{COMFY_HOST}/ws"

# Tunables
JOB_TIMEOUT_S = int(os.environ.get("JOB_TIMEOUT_S", "600"))
POLL_INTERVAL_S = 0.25


def _wait_for_comfy(timeout_s: int = 120) -> None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            r = requests.get(f"{COMFY_HTTP}/system_stats", timeout=2)
            if r.ok:
                return
        except requests.RequestException:
            pass
        time.sleep(0.5)
    raise RuntimeError("ComfyUI did not become ready in time")


def _upload_images(images: list[dict]) -> None:
    for item in images:
        name = item["name"]
        blob = base64.b64decode(item["image"])
        files = {"image": (name, BytesIO(blob), "image/png")}
        data = {"overwrite": "true"}
        r = requests.post(f"{COMFY_HTTP}/upload/image", files=files, data=data, timeout=30)
        r.raise_for_status()


def _queue_prompt(workflow: dict, client_id: str) -> str:
    payload = {"prompt": workflow, "client_id": client_id}
    r = requests.post(f"{COMFY_HTTP}/prompt", json=payload, timeout=30)
    if not r.ok:
        raise RuntimeError(f"ComfyUI rejected prompt: {r.status_code} {r.text}")
    return r.json()["prompt_id"]


def _wait_for_completion(prompt_id: str, client_id: str) -> dict:
    """Block until the prompt finishes. Uses websocket for liveness, history for the result."""
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


def _fetch_image(filename: str, subfolder: str, folder_type: str) -> bytes:
    q = urllib.parse.urlencode({"filename": filename, "subfolder": subfolder, "type": folder_type})
    r = requests.get(f"{COMFY_HTTP}/view?{q}", timeout=60)
    r.raise_for_status()
    return r.content


def _collect_outputs(history: dict, return_mode: str) -> list[dict]:
    outputs = []
    for node_id, node_out in history.get("outputs", {}).items():
        for img in node_out.get("images", []) or []:
            data = _fetch_image(img["filename"], img.get("subfolder", ""), img.get("type", "output"))
            entry = {"node": node_id, "filename": img["filename"]}
            if return_mode == "url":
                # No object store wired up — fall back to base64 with a flag.
                entry["image"] = base64.b64encode(data).decode()
                entry["note"] = "return=url not configured; returned base64"
            else:
                entry["image"] = base64.b64encode(data).decode()
            outputs.append(entry)
    return outputs


def handler(job: dict) -> dict:
    job_input = job.get("input") or {}
    workflow = job_input.get("workflow")
    if not workflow:
        return {"error": "input.workflow is required (ComfyUI API-format JSON)"}

    images = job_input.get("images") or []
    return_mode = job_input.get("return", "base64")

    _wait_for_comfy()
    if images:
        _upload_images(images)

    client_id = str(uuid.uuid4())
    prompt_id = _queue_prompt(workflow, client_id)
    history = _wait_for_completion(prompt_id, client_id)
    outputs = _collect_outputs(history, return_mode)

    return {"prompt_id": prompt_id, "images": outputs}


if __name__ == "__main__":
    runpod.serverless.start({"handler": handler})
