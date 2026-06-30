VLLM_ATTENTION_BACKEND=TORCH_SDPA \
TOKENIZERS_PARALLELISM=false python -m vllm.entrypoints.openai.api_server \
    --model google/gemma-4-31b-it \
    --port 8000 \
    --dtype bfloat16 \
    --max-model-len 16384 \
    --trust-remote-code \
    --enable-auto-tool-choice \
    --tool-call-parser gemma4 
