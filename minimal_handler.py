"""Absolute minimum handler — no ComfyUI, no waits, just returns. Used to isolate
whether the issue is ours or RunPod infra. Activated by START_MINIMAL=1 in start.sh.
"""
import os, socket, runpod

def handler(job):
    return {
        "status": "minimal handler responded",
        "hostname": socket.gethostname(),
        "env_keys": sorted([k for k in os.environ.keys() if not k.startswith("LD_")])[:30],
        "input": job.get("input"),
    }

if __name__ == "__main__":
    print("[minimal_handler] starting", flush=True)
    runpod.serverless.start({"handler": handler})
