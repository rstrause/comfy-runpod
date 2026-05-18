FROM nvidia/cuda:12.4.1-cudnn-runtime-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_NO_CACHE_DIR=1 \
    PYTHONUNBUFFERED=1 \
    COMFY_PORT=8188

RUN apt-get update && apt-get install -y --no-install-recommends \
        python3.10 python3-pip python3.10-venv \
        git wget curl ca-certificates libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

RUN ln -sf /usr/bin/python3.10 /usr/bin/python && \
    python -m pip install --upgrade pip

WORKDIR /

# ComfyUI — pinned to a known-good commit so rebuilds are reproducible.
# Bump COMFY_REF when you want a newer version.
ARG COMFY_REF=v0.3.27
RUN git clone https://github.com/comfyanonymous/ComfyUI.git /ComfyUI && \
    cd /ComfyUI && git checkout ${COMFY_REF} && \
    sed -i 's/comfyui-frontend-package==1.14.5/comfyui-frontend-package==1.14.6/' requirements.txt

# Torch for CUDA 12.4
RUN pip install --extra-index-url https://download.pytorch.org/whl/cu124 \
        torch==2.5.1 torchvision==0.20.1 torchaudio==2.5.1

RUN pip install -r /ComfyUI/requirements.txt

# RunPod SDK + handler deps
RUN pip install runpod==1.7.7 requests websocket-client pillow

# Point ComfyUI at the network volume
COPY extra_model_paths.yaml /ComfyUI/extra_model_paths.yaml

COPY start.sh /start.sh
COPY handler.py /handler.py
COPY test_input.json /test_input.json
RUN chmod +x /start.sh

# Optional: bake any custom_nodes here, e.g.:
# RUN cd /ComfyUI/custom_nodes && git clone https://github.com/ltdrdata/ComfyUI-Manager.git

CMD ["/start.sh"]
