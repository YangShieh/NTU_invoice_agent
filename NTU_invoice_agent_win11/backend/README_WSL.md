# vLLM backend on Windows 11 / WSL2

This targets Ubuntu 22.04 in WSL2 and one RTX 4070 Ti Super (16 GB). It serves
the 4-bit AWQ Gemma 4 12B vision model; the former 31B bfloat16 configuration
does not fit this GPU.

## Host prerequisites

1. Install a current NVIDIA Windows driver. Do **not** install a Linux NVIDIA
   display driver inside WSL; WSL exposes the Windows driver to Linux.
2. In elevated Windows PowerShell, run `wsl --update`, then confirm Ubuntu is
   version 2 with `wsl --list --verbose`.
3. In Ubuntu, confirm the GPU is visible with `nvidia-smi`.

The vLLM pip wheel includes its CUDA/PyTorch runtime, so a separate CUDA Toolkit
is normally unnecessary. If compilation requires one, use NVIDIA's WSL package
or `cuda-toolkit-12-x`, never `cuda-drivers`.

## Install

Keep the repository in the WSL Linux filesystem (for example `~/src`), not
`/mnt/c`, for better model-cache and Python I/O performance.

```bash
sudo apt update
sudo apt install -y libzbar0 libgl1 libglib2.0-0
cd backend
conda env create -f environment.yml
conda activate ntu-invoice-vllm
python -m pip check
```

`libzbar0` is required by `pyzbar`; the GL/GLib libraries support QReader's
OpenCV dependency. These system libraries cannot be installed through pip or
the provided Conda environment. vLLM and the Python packages are intentionally
installed together by pip inside an otherwise clean Conda environment so pip
can select one mutually compatible PyTorch/CUDA dependency graph.

## Run

Start vLLM in terminal 1 (the first run downloads the model):

```bash
cd backend
conda activate ntu-invoice-vllm
./vllm_4bit.sh
```

Wait for port 8080, then verify it with `curl http://localhost:8080/v1/models`.
Start the application in terminal 2:

```bash
cd backend
conda activate ntu-invoice-vllm
python api.py
```

Test it with:

```bash
curl -F 'file=@/path/to/invoice.jpg' http://localhost:8000/extract
```

If startup runs out of VRAM, close GPU-using Windows applications and retry:

```bash
MAX_MODEL_LEN=2048 GPU_MEMORY_UTILIZATION=0.85 ./vllm_4bit.sh
```

The default allows one concurrent sequence. Increase concurrency only after
measuring free VRAM with `nvidia-smi`.

The launcher enables vLLM's `gemma4` tool-call parser. This is required by the
browser agent in `frontend/agent.py`, which sends OpenAI-compatible `tools` with
`tool_choice="auto"` and expects structured `message.tool_calls`. The Gemma 4
reasoning parser is deliberately not enabled because this project does not
request thinking-mode output.
