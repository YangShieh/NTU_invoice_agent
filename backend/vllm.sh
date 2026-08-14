#!/usr/bin/env bash
set -euo pipefail

# Compatibility wrapper. The former 31B/bfloat16 configuration requires far
# more than the 16 GB VRAM available on an RTX 4070 Ti Super.
exec "$(dirname "$0")/vllm_4bit.sh" "$@"
