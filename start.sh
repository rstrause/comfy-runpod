#!/usr/bin/env bash
set -euo pipefail

# Start ComfyUI in the background, bound to localhost only.
echo "[start.sh] launching ComfyUI on :${COMFY_PORT}"
python /ComfyUI/main.py \
    --listen 127.0.0.1 \
    --port "${COMFY_PORT}" \
    --disable-auto-launch \
    --disable-metadata \
    &

# Wait for ComfyUI to be ready before accepting jobs.
for i in $(seq 1 120); do
    if curl -sf "http://127.0.0.1:${COMFY_PORT}/system_stats" >/dev/null; then
        echo "[start.sh] ComfyUI is up"
        break
    fi
    sleep 1
done

echo "[start.sh] starting RunPod handler"
exec python -u /handler.py
