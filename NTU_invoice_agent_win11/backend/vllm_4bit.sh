#!/usr/bin/env bash
set -euo pipefail

MODEL_NAME="${MODEL_NAME:-mattbucci/gemma-4-12B-AWQ}"
VLLM_PORT="${VLLM_PORT:-8080}"
MAX_MODEL_LEN="${MAX_MODEL_LEN:-8192}"
GPU_MEMORY_UTILIZATION="${GPU_MEMORY_UTILIZATION:-0.80}"

export TOKENIZERS_PARALLELISM="${TOKENIZERS_PARALLELISM:-false}"
export HF_HUB_DISABLE_XET="${HF_HUB_DISABLE_XET:-1}"
export VLLM_USE_V2_MODEL_RUNNER="${VLLM_USE_V2_MODEL_RUNNER:-0}"
export VLLM_USE_FLASHINFER_SAMPLER="${VLLM_USE_FLASHINFER_SAMPLER:-0}"

exec vllm serve "${MODEL_NAME}" \
    --host 0.0.0.0 \
    --port "${VLLM_PORT}" \
    --dtype auto \
    --quantization awq \
    --max-model-len "${MAX_MODEL_LEN}" \
    --gpu-memory-utilization "${GPU_MEMORY_UTILIZATION}" \
    --max-num-seqs 1 \
    --mm-processor-kwargs '{"max_soft_tokens": 280}' \
    --enable-auto-tool-choice \
    --tool-call-parser gemma4 \
    --trust-remote-code
