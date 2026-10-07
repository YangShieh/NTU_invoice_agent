# NTU Invoice Agent System — 希望

## Windows 11 一鍵啟動

可將 `START_WIN11.bat` 複製到 Windows 桌面後雙擊，並將實際專案資料夾命名為 `NTU_invoice_agent`、放在同一個桌面。啟動器會自動啟動並持續監控 WSL vLLM、OCR API、前端伺服器與 Electron；任何程序意外結束後都會自動重啟。Electron 每次啟動時會先清除上一輪資料，再從主頁開始。

預設 WSL distribution 與 Conda 環境名稱可在 `win11_supervisor.ps1` 的參數中調整。完整說明請參閱上一層的 `START_HERE_WSL.md`。

An intelligent, hybrid automation tool designed to streamline the invoice submission process for the legacy NTU Accounting System. By combining an intuitive web frontend, an advanced local Vision AI backend powered by vLLM, and a headless browser automation agent, this project drastically cuts down manual data entry and ensures high accuracy.

---

## 📖 Table of Contents
1. [Architecture Overview](#architecture-overview)
2. [Key Features](#key-features)
3. [Prerequisites](#prerequisites)
4. [Installation & Setup](#installation--setup)
5. [Usage Guide](#usage-guide)
6. [AI Backend & OCR Workflow](#ai-backend--ocr-workflow)

---

## 🏛️ Architecture Overview
This system operates across three distinct layers:
1. **Frontend UI (`session_ui.py`)**: A modern HTML/JS PWA interface running locally (Port 8001). Features a「希望」landing page, handles image uploads, live camera feeds, editable OCR review, and automatic amount capping.
2. **AI Vision Backend (`vllm.sh`, `api.py`, `gemma.py`)**: A local AI extraction pipeline. It runs the powerful `google/gemma-4-31b-it` model via vLLM to classify invoices, decode QR codes, and intelligently extract line items.
3. **Execution Agent (`agent.py`)**: A Python-based Playwright script that runs Chromium in **headless mode**, securely injects credentials, navigates the NTU accounting system, submits data, and opens the final print page (`printatt.asp`) in a visible browser for printing.

---

## ✨ Key Features
- **PWA Landing Page**: A beautiful「希望」branded landing page — installable as a home screen app on any device.
- **Local AI OCR (Gemma 4)**: Uses a local vLLM instance to run `gemma-4-31b-it` for highly accurate, private invoice extraction. No cloud APIs required.
- **Hybrid QR Validation**: Automatically detects Taiwan e-invoices, scans the embedded QR code for 100% accurate totals and dates, and merges it with the AI's itemized extraction.
- **Live Document Camera**: Hot-swap between laptop webcams and external USB Document Cameras (實物攝影機) natively in the browser.
- **Editable OCR Items**: All extracted line items (品名/數量/單價) are fully editable with add/remove row support before submission.
- **Automatic Amount Capping**: Amounts exceeding NT$2,000 are automatically capped to 2,000.
- **Smart Invoice Numbers**: When the OCR cannot find an invoice number (`NOT FOUND`), it auto-generates one using ROC date format (e.g. `1150703` for 2026/07/03).
- **Headless Automation**: The browser runs entirely in the background — users never see the automation process. Only the final print page (`printatt.asp`) is opened in a visible browser.
- **Legacy System Bypassing**: Automatically navigates heavily-framed legacy DOMs, using raw JavaScript injection to bypass `readonly` fields.
- **Zero-Trace Data Wiping**: Clicking "Wipe" permanently zeros-out and deletes all local JSON, images, and PDF files.

---

## ⚙️ Prerequisites
With INT4 AWQ quantization, the system runs comfortably on a single consumer GPU.

**Hardware Requirements (AI Backend):**
- **GPU (Recommended — INT4 AWQ)**: A single GPU with **24GB+ VRAM** is sufficient.
  - Model weights: ~16GB, leaving ample room for KV cache.
  - *Examples*: NVIDIA RTX 5090 (32GB), RTX 4090 (24GB).
  - Quality loss is <3% and negligible for structured OCR/invoice tasks.
- **GPU (Optional — Full BF16)**: For unquantized full precision, ~64GB+ VRAM is required (e.g. 2x A6000, 1x A100/H100).
- **RAM**: 32GB+ System RAM.
- **Storage**: 50GB+ for quantized model weights (100GB+ for full precision).

**Software Requirements:**
- **OS**: Linux (Ubuntu 20.04/22.04 recommended) or macOS (Apple Silicon).
- **Python**: 3.11 (managed via Conda)
- **Conda**: Anaconda or Miniconda
- **CUDA**: CUDA Toolkit 12.1+ (if using NVIDIA GPUs).

---

## 🚀 Installation & Setup

### 0. Create Conda Environment
We recommend using Conda with Python 3.11 to manage all dependencies cleanly. You can create one environment for each component if needed:
```bash
conda create -n invoice_agent python=3.11 -y
conda activate invoice_agent
```

### 1. Install Python Dependencies
The project dependencies are split into two groups, each with its own `requirements.txt`.

**A. Frontend & Automation Agent (`frontend/`)**
```bash
cd frontend
pip install -r requirements.txt
playwright install chromium
```
- `fastapi` & `uvicorn`: Powers the lightweight, asynchronous web server.
- `playwright`: Navigates the legacy NTU system and injects JavaScript.
- `pydantic` & `python-multipart`: Strict data validation and parsing binary camera uploads.
- `requests` & `openai`: HTTP calls to the backend API and LLM client for the agent loop.

**B. AI Vision Backend (`backend/`)**
> ⚠️ **Important:** Install `pyzbar` via **conda** (not pip) to automatically include the required `zbar` system library.
```bash
cd backend
conda install -c conda-forge pyzbar -y
pip install -r requirements.txt
```
- `pyzbar`: Must be installed via conda to bundle the `zbar` C shared library. Installing via pip alone will cause `ImportError: Unable to find zbar shared library`.
- `vllm` & `torch`: The ultra-fast local inference engine that loads and runs the 31B Gemma model in GPU VRAM using PyTorch.
- `opencv-python` (`cv2`) & `Pillow` (`PIL`): Handles image processing, resizing, and manipulation before passing it to the OCR model.
- `qreader`: Advanced QR code decoding for reading Taiwan e-invoice QR codes with 100% accuracy.
- `opencc`: Performs Simplified to Traditional Chinese text conversion.
- `openai`: Used as a standard client to communicate with the local `vLLM` server.

### 2. Configure Credentials
Create `frontend/config.json`:
```json
{
    "ntu_username": "YOUR_NTU_ID",
    "ntu_password": "YOUR_NTU_PASSWORD",
    "agent_base_url": "YOUR_AGENT_URL_IF_NEEDED",
    "project_code": "114L3073",
    "expense_type": "教材費(書籍)"
}
```

### 3. Start the AI Vision Backend
Launch the vLLM server:
```bash
cd backend
bash vllm.sh
```
*Wait for the model weights to load into VRAM.* Then start the extraction API:
```bash
python api.py
```

For **RTX 5090 (32GB)** users, use INT4 AWQ quantization:
```bash
VLLM_ATTENTION_BACKEND=TORCH_SDPA \
python -m vllm.entrypoints.openai.api_server \
    --model google/gemma-4-31b-it-awq \
    --port 8000 \
    --dtype auto \
    --quantization awq \
    --max-model-len 8192 \
    --trust-remote-code
```

### 4. Start the Web UI
```bash
cd frontend
python session_ui.py
```

---

## 🧠 AI Backend & OCR Workflow
The OCR pipeline is explicitly designed for Taiwan's complex receipt landscape:
1. **Classification**: When an image is captured, `api.py` asks Gemma to classify it as an `einvoice` (電子發票), `old` (傳統發票), or `hand` (手寫收據).
2. **QR Verification**: If classified as `einvoice`, `gemma.py` runs a strict QR code scanner to extract the exact Invoice Number, Date, and Total Amount, bypassing AI hallucinations.
3. **Deep Extraction**: The image and a tailored prompt are sent to Gemma-4-31b-it to extract individual line items, quantities, and descriptions.
4. **Data Merging**: The 100% accurate QR data is merged with the LLM's itemized JSON output, validated, and returned to the frontend.

---

## 📝 Usage Guide
1. Open `http://localhost:8001` — tap the「希望」icon to enter the app.
2. **Step 1**: Enter the Payee Information (ID, Name, Bank Code, Account Number).
3. **Step 2**: Click **"📷 使用電腦相機拍照"** to snap a photo of your receipt, or drag-and-drop an image.
4. **Step 3**: Review and edit the extracted data. Items are fully editable. Amounts over NT$2,000 are auto-capped. Invoices without a number auto-generate a ROC date format number.
5. **Step 4**: Click **Execute**. The Playwright agent runs in the background (headless). Upon completion, the NTU print page (`printatt.asp`) opens in a visible browser — press **Ctrl+P** to print.
6. **Step 5**: After printing, click **Wipe Data** to securely erase all personal data from the device.
