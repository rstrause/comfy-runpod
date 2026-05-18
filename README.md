# ComfyUI on RunPod Serverless

Files:
- `Dockerfile` — CUDA 12.4 + ComfyUI v0.3.27 + RunPod SDK
- `extra_model_paths.yaml` — tells ComfyUI to read models from `/runpod-volume/models/...`
- `start.sh` — boots ComfyUI on 127.0.0.1:8188, then runs the handler
- `handler.py` — queues a workflow, waits via websocket, returns base64 PNG(s)
- `test_input.json` — sample SD1.5 workflow

---

## 1. Get a RunPod API key

1. Log in at https://www.runpod.io/console/user/settings
2. **API Keys → Create API Key** → "Read & Write" → copy it.
3. Export locally so `runpodctl` and curl examples work:
   ```sh
   setenv RUNPOD_API_KEY  pa_XXXXXXXXXXXXXXXX   # csh / tcsh
   # or in bash:
   export RUNPOD_API_KEY=pa_XXXXXXXXXXXXXXXX
   ```

## 2. Create a Network Volume

Console → **Storage → New Network Volume**
- Region: pick one with the GPUs you want (e.g. `US-NJ-1`, `EU-RO-1`). Volumes are region-locked.
- Size: enough for all your checkpoints + LoRAs (start at 50–100 GB; expandable).
- Name: e.g. `comfy-models`.

Note the **Volume ID** — you'll attach it to the endpoint later.

## 3. Upload your local models to the volume

Easiest path: spin up a **cheap CPU pod** with the volume attached, then `scp` / `rsync`.

1. Console → **Pods → Deploy** → pick a "CPU" or smallest GPU pod in the **same region** as the volume.
2. Under "Network Volume", select `comfy-models`. It mounts at `/workspace`.
3. Once running, click "Connect" → copy the SSH command, then from your machine:
   ```sh
   ssh root@<pod-host> -p <port> -i ~/.ssh/id_ed25519 \
       'mkdir -p /workspace/models/checkpoints /workspace/models/loras /workspace/models/vae'

   rsync -avhP /path/to/local/models/checkpoints/ \
       root@<pod-host>:/workspace/models/checkpoints/ -e 'ssh -p <port>'
   ```
4. Verify layout matches `extra_model_paths.yaml`:
   ```
   /workspace/models/checkpoints/<your>.safetensors
   /workspace/models/loras/...
   /workspace/models/vae/...
   ```
5. Terminate the helper pod when done (volume persists).

> Alt: `runpodctl` also supports `send`/`receive`, but rsync over SSH is faster for many files.

## 4. Build & push the Docker image (via GitHub Actions)

Arizona has no Docker, so we build on GitHub-hosted runners. The repo includes `.github/workflows/build.yml` which builds and pushes to `ghcr.io/rstrause/comfy-runpod:latest` on every push to `main`.

### One-time setup

```sh
cd /mnt/u2/users/rstrause/claude_projects/comfy_dockerImage

# configure git identity (only if you've never done this on this machine)
git config --global user.name  "Rina Strause"
git config --global user.email "rina.osamura@gmail.com"

# init repo
git init -b main
git add .
git commit -m "Initial ComfyUI RunPod image"
```

Create the GitHub repo:

1. https://github.com/new
2. **Repository name**: `comfy-runpod`
3. **Visibility**: Public (simpler — the image becomes pullable without auth) or Private (you'll need a PAT on RunPod)
4. **Do NOT** initialize with README/gitignore (we already have files)
5. Create repository

Add the remote and push:
```sh
git remote add origin https://github.com/rstrause/comfy-runpod.git
git push -u origin main
```

GitHub will prompt for credentials — use a **Personal Access Token** as the password:
- https://github.com/settings/tokens?type=beta → Generate new token (fine-grained)
- Repository access: `comfy-runpod` only
- Permissions: `Contents: Read and write`, `Packages: Read and write`
- Copy the token, use it when git prompts for password

### Watch the build

1. Go to https://github.com/rstrause/comfy-runpod/actions
2. The workflow `build-and-push` runs automatically (~8–12 min)
3. Once green, your image lives at `ghcr.io/rstrause/comfy-runpod:latest`

### Make the image public (so RunPod can pull without auth)

1. https://github.com/rstrause?tab=packages
2. Click `comfy-runpod` → **Package settings** (right sidebar)
3. Scroll to **Danger Zone** → **Change package visibility** → **Public** → confirm

(If you keep it private, see §5 footnote for adding a registry credential on RunPod.)

### Iterating

Every `git push` to `main` that touches Dockerfile/handler/scripts triggers a rebuild. After the build finishes, go to the RunPod endpoint → **Release new version** to roll the workers.

## 5. Create the Serverless Endpoint

Console → **Serverless → New Endpoint**
- **Container image**: `ghcr.io/rstrause/comfy-runpod:latest`
- **Container disk**: 20 GB (image + scratch)
- **GPU**: pick by VRAM you need (24 GB for SDXL, 16 GB ok for SD1.5)
- **Workers**: Min 0 / Max 1–3 to start. Min 0 = scale to zero, pay only for active.
- **Network Volume**: attach `comfy-models` (must be the same region as workers).
- **Container start command**: leave blank (Dockerfile `CMD` handles it).
- **Environment variables** (optional): `JOB_TIMEOUT_S=600`

Save → note the **Endpoint ID** (e.g. `abcd1234efgh`).

## 6. Fire a test job

```sh
ENDPOINT_ID=abcd1234efgh

# async submit
curl -s -X POST "https://api.runpod.ai/v2/${ENDPOINT_ID}/run" \
    -H "Authorization: Bearer ${RUNPOD_API_KEY}" \
    -H "Content-Type: application/json" \
    -d @test_input.json
# → {"id":"<job-id>","status":"IN_QUEUE"}

# poll
curl -s "https://api.runpod.ai/v2/${ENDPOINT_ID}/status/<job-id>" \
    -H "Authorization: Bearer ${RUNPOD_API_KEY}" | jq

# or sync (blocks up to 30s, then 202)
curl -s -X POST "https://api.runpod.ai/v2/${ENDPOINT_ID}/runsync" \
    -H "Authorization: Bearer ${RUNPOD_API_KEY}" \
    -H "Content-Type: application/json" \
    -d @test_input.json | jq '.output.images[0] | {node, filename}'
```

Decode an image from the response:
```sh
jq -r '.output.images[0].image' resp.json | base64 -d > out.png
```

## 7. Iterating

- New ComfyUI version: bump `COMFY_REF` in the Dockerfile, rebuild, push, then **Endpoint → Release new version**.
- New custom node: add a `RUN cd /ComfyUI/custom_nodes && git clone ...` line. (Or drop it into `custom_nodes/` on the volume — `extra_model_paths.yaml` already adds that path.)
- New models: just upload to the volume; no rebuild needed.

## Troubleshooting

- **Worker stuck "Initializing"**: image is still pulling. First pull on a fresh host is slow.
- **`Checkpoint not found`**: filename in `test_input.json` doesn't match `/runpod-volume/models/checkpoints/`. Case-sensitive.
- **Timeouts**: bump `JOB_TIMEOUT_S` env var and the endpoint's "Execution Timeout".
- **OOM**: pick a bigger GPU tier, or reduce batch size / resolution in the workflow.
- **No GPUs available**: the region is dry. Either wait or recreate the volume in another region (volumes are region-locked, no in-place move).
