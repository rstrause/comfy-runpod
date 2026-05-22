#!/usr/bin/env bash
# Lightweight startup — just exec the handler. ComfyUI is lazy-started by the
# handler on the first job that needs it, so the handler is immediately
# responsive to RunPod health checks and diagnostic calls.

echo "[start.sh] START_MINIMAL=${START_MINIMAL:-0}"

if [ "${START_MINIMAL:-0}" = "1" ]; then
    echo "[start.sh] MINIMAL mode"
    exec python -u /minimal_handler.py
fi

echo "[start.sh] starting handler (ComfyUI will spawn lazily on first workflow request)"
exec python -u /handler.py
