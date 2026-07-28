export VLLM_USE_V1=0
export FLASHINFER_DISABLE_VERSION_CHECK=1
export VLLM_ATTENTION_BACKEND=TORCH_SDPA
export TOKENIZERS_PARALLELISM=false

python -m vllm.entrypoints.openai.api_server \
    --model mattbucci/gemma-4-12B-AWQ \
    --port 8080 \
    --max-model-len 8192 \
    --gpu-memory-utilization 0.70 \
    --trust-remote-code \
    --dtype auto \
    --quantization awq \
    --enable-auto-tool-choice \
    --tool-call-parser gemma4