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

# Start ComfyUI in the background, bound to localhost only.
# Capture its output to /tmp/comfyui.log AND mirror to stderr so worker logs show it.
echo "[start.sh] launching ComfyUI on :${COMFY_PORT}"
( python -u /ComfyUI/main.py \
    --listen 127.0.0.1 \
    --port "${COMFY_PORT}" \
    --disable-auto-launch \
    --disable-metadata \
    2>&1 | tee /tmp/comfyui.log ) &
COMFY_PID=$!

# Wait up to STARTUP_TIMEOUT seconds for ComfyUI to be ready.
# Default 300s to allow cold network-volume model scanning.
STARTUP_TIMEOUT="${COMFY_STARTUP_TIMEOUT:-300}"
echo "[start.sh] waiting up to ${STARTUP_TIMEOUT}s for ComfyUI to be ready..."
READY=0
for i in $(seq 1 "${STARTUP_TIMEOUT}"); do
    if curl -sf "http://127.0.0.1:${COMFY_PORT}/system_stats" >/dev/null 2>&1; then
        echo "[start.sh] ComfyUI is up after ${i}s"
        READY=1
        break
    fi
    # Every 30s, also check the comfy process is still alive
    if [ $((i % 30)) -eq 0 ]; then
        if ! kill -0 "$COMFY_PID" 2>/dev/null; then
            echo "[start.sh] ERROR: ComfyUI process died — last 80 lines of log:"
            tail -n 80 /tmp/comfyui.log || true
            # Don't exit: still start the handler so it can return the log
            # via the job response (handler reads /tmp/comfyui.log on _wait_for_comfy timeout).
            break
        fi
        echo "[start.sh] still waiting (${i}s)..."
    fi
    sleep 1
done

if [ "$READY" != "1" ]; then
    echo "[start.sh] ERROR: ComfyUI did not become ready in ${STARTUP_TIMEOUT}s — last 80 lines of log:"
    tail -n 80 /tmp/comfyui.log || true
fi

echo "[start.sh] starting RunPod handler"
exec python -u /handler.py
