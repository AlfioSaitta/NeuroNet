# AI Ecosystem — Session Summary

## Goal
Rebuild Jarvis worker container with CUDA 13.0 toolkit overlay to match host driver (NVIDIA 580.159.03) and enable GPU inference via llama-cpp-python.

## Constraints & Preferences
- llama-cpp-python built from GitHub main branch for Gemma 4 fix (PR #22133)
- RTX 3050 Ti with 4GB VRAM, compute capability 8.6
- Python 3.11, Ubuntu 22.04 compatibility preserved
- Slow internet connection; minimize download size
- Model files (8.7GB) excluded from Docker build context via `.dockerignore`

## Done
- Root cause identified: CUDA 12.2 container runtime incompatible with host CUDA 13.0 driver (NVIDIA 580.159.03)
- `.env` updated: `N_GPU_LAYERS=0→15`, `LLM_FLASH_ATTN=false→true`
- `jarvis/Dockerfile` updated: added CUDA 13.0 keyring, `cuda-compiler-13-0`, `cuda-cudart-dev-13-0`, `libcublas-dev-13-0` apt packages on CUDA 12.2 base
- `llama-cpp-python==0.3.31` built from source with `-DGGML_CUDA=on -DCMAKE_CUDA_ARCHITECTURES=86` against CUDA 13.0 — wheel installed (270MB)
- `granian` installed via pip for ASGI server
- Disk space fixed: pruned 99GB Docker data, removed old 36GB image
- `.dockerignore` created excluding `jarvis/models/` from build context — CONTENT SIZE 19.6GB
- Container running successfully with GPU inference verified
- API test: `/v1/chat/completions` → correct response ("La capitale della Francia è Parigi.")
- Zero CUDA/GPU errors in logs

## Current State
- **Container**: `jarvis_worker` running, GPU accelerated
- **GPU**: Chat model 47%, Embed model 57% VRAM; 86°C peak after inference (under 89°C threshold)
- **Model**: Qwen3.5-4B-UD-Q4_K_XL.gguf with `n_gpu_layers=15`, `flash_attn=True`
- **Final image**: `ai-ecosystem-jarvis_worker:latest` — DISK USAGE 47.4GB / CONTENT SIZE 19.6GB
- **Only warnings**: web_search DNS errors (expected, searxng not running)

## Key Decisions
- `libcublas-dev-13-0` (840MB) required when llama.cpp cmake needed `CUDA::cublas` target
- Models excluded via `.dockerignore` to reduce build context by 8.7GB
- `|| true` on `spacy download` step to avoid build failure on transient DNS issues

## Next Steps
1. Start searxng/crawl4ai for web search capability (optional)
2. Monitor GPU temperature during sustained inference (target <89°C)
3. Connect master node if setting up distributed mode

## Relevant Files
- `jarvis/Dockerfile`: CUDA 13.0 overlay, llama-cpp-python GPU build
- `jarvis/.env`: `N_GPU_LAYERS=15`, `LLM_FLASH_ATTN=true`
- `docker-compose.worker.yml`: GPU reservations, volume mounts
- `.dockerignore`: excludes `**/models/`, `.git/`, `__pycache__/`, `*.pyc`, `.env`
- `jarvis/llm_engine.py`: loads model with `n_gpu_layers` from env
- `jarvis/config.py`: reads `N_GPU_LAYERS`, `LLM_FLASH_ATTN`, `LLAMA_MODEL_PATH`
