#!/usr/bin/env bash
# NO set -e — we want to keep going on errors so we can see them.

echo "[start.sh] booting; START_MINIMAL=${START_MINIMAL:-0}"

if [ "${START_MINIMAL:-0}" = "1" ]; then
    echo "[start.sh] MINIMAL mode — skipping ComfyUI, running minimal handler"
    exec python -u /minimal_handler.py
fi

# Normal mode: wire input/output dirs, launch ComfyUI in background, exec handler.
if [ -d /runpod-volume ]; then
    echo "[start.sh] /runpod-volume is mounted, wiring input/output dirs"
    mkdir -p /runpod-volume/input /runpod-volume/output || echo "[start.sh] mkdir failed"
    rm -rf /ComfyUI/input /ComfyUI/output 2>/dev/null || true
    ln -sf /runpod-volume/input  /ComfyUI/input  || echo "[start.sh] ln input failed"
    ln -sf /runpod-volume/output /ComfyUI/output || echo "[start.sh] ln output failed"
else
    echo "[start.sh] WARNING: /runpod-volume not mounted"
fi

echo "[start.sh] launching ComfyUI on :${COMFY_PORT:-8188} (background)"
python -u /ComfyUI/main.py \
    --listen 127.0.0.1 \
    --port "${COMFY_PORT:-8188}" \
    --disable-auto-launch \
    --disable-metadata \
    > /tmp/comfyui.log 2>&1 &

echo "[start.sh] starting RunPod handler immediately"
exec python -u /handler.py
