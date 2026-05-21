#!/usr/bin/env bash
set -euo pipefail

# Make ComfyUI's input/output dirs live on the network volume so files persist
# across worker spawns and can be pre-uploaded via SSH.
if [ -d /runpod-volume ]; then
    echo "[start.sh] /runpod-volume is mounted, wiring input/output dirs"
    mkdir -p /runpod-volume/input /runpod-volume/output
    rm -rf /ComfyUI/input /ComfyUI/output
    ln -sf /runpod-volume/input  /ComfyUI/input
    ln -sf /runpod-volume/output /ComfyUI/output
else
    echo "[start.sh] WARNING: /runpod-volume not mounted; using container-local input/output"
fi

# Start ComfyUI in the background. We do NOT wait for it here — that would
# delay the handler from being exposed and RunPod marks the worker unhealthy.
# Instead, the handler's per-request _wait_for_comfy() handles the wait when
# a job actually needs ComfyUI.
echo "[start.sh] launching ComfyUI on :${COMFY_PORT} (background)"
( python -u /ComfyUI/main.py \
    --listen 127.0.0.1 \
    --port "${COMFY_PORT}" \
    --disable-auto-launch \
    --disable-metadata \
    2>&1 | tee /tmp/comfyui.log ) &

echo "[start.sh] starting RunPod handler immediately (ComfyUI continues to boot in background)"
exec python -u /handler.py
