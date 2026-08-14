#!/usr/bin/env bash
set -euo pipefail

# Sized for one RTX 4070 Ti Super (16 GB). Values can be overridden, e.g.
# MAX_MODEL_LEN=2048 GPU_MEMORY_UTILIZATION=0.85 ./vllm_4bit.sh
MODEL_NAME="${MODEL_NAME:-mattbucci/gemma-4-12B-AWQ}"
VLLM_PORT="${VLLM_PORT:-8080}"
MAX_MODEL_LEN="${MAX_MODEL_LEN:-4096}"
GPU_MEMORY_UTILIZATION="${GPU_MEMORY_UTILIZATION:-0.90}"

export TOKENIZERS_PARALLELISM="${TOKENIZERS_PARALLELISM:-false}"

exec vllm serve "${MODEL_NAME}" \
    --host 0.0.0.0 \
    --port "${VLLM_PORT}" \
    --dtype auto \
    --quantization awq \
    --max-model-len "${MAX_MODEL_LEN}" \
    --gpu-memory-utilization "${GPU_MEMORY_UTILIZATION}" \
    --max-num-seqs 1 \
    --enable-auto-tool-choice \
    --tool-call-parser gemma4 \
    --trust-remote-code
