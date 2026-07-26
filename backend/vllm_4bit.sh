VLLM_ATTENTION_BACKEND=TORCH_SDPA \
TOKENIZERS_PARALLELISM=false python -m vllm.entrypoints.openai.api_server \
    --model cyankiwi/gemma-4-31B-it-AWQ-4bit \
    --port 8080 \
    --max-model-len 8192 \
    --gpu-memory-utilization 0.70 \
    --trust-remote-code \
    --enable-auto-tool-choice \
    --tool-call-parser gemma4