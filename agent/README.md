# NTU Invoice Agent System

An intelligent, hybrid automation tool designed to streamline the invoice submission process for the legacy NTU Accounting System. By combining an intuitive web frontend, an advanced local Vision AI backend powered by vLLM, and a headless browser automation agent, this project drastically cuts down manual data entry and ensures high accuracy.

---

## 📖 Table of Contents
1. [Architecture Overview](#architecture-overview)
2. [Key Features](#key-features)
3. [Prerequisites](#prerequisites)
4. [Installation & Setup](#installation--setup)
5. [Usage Guide](#usage-guide)
6. [AI Backend & OCR Workflow](#ai-backend--ocr-workflow)
7. [API Reference](#api-reference)

---

## 🏛️ Architecture Overview
This system operates across three distinct layers:
1. **Frontend UI (`session_ui.py`)**: A modern HTML/JS interface running locally (Port 8000). It acts as the data-collection hub, handling image uploads, live camera feeds, and user review of OCR data.
2. **AI Vision Backend (`vllm.sh`, `api.py`, `gemma.py`)**: A local AI extraction pipeline. It runs the powerful `google/gemma-4-31b-it` model via vLLM (Port 8000/8080) to classify invoices, decode QR codes, and intelligently extract line items.
3. **Execution Agent (`agent.py`)**: A Python-based Playwright script that spins up a Chromium browser, securely injects credentials, and mimics human interaction to navigate the NTU accounting system and submit the data.

---

## ✨ Key Features
- **Local AI OCR (Gemma 4)**: Uses a local vLLM instance to run `gemma-4-31b-it` for highly accurate, private invoice extraction. No cloud APIs required!
- **Hybrid QR Validation**: Automatically detects Taiwan e-invoices, scans the embedded QR code for 100% accurate totals and dates, and merges it with the AI's itemized extraction.
- **Live Document Camera**: Hot-swap between laptop webcams and external USB Document Cameras (實物攝影機) natively in the browser.
- **Legacy System Bypassing**: Automatically navigates heavily-framed legacy DOMs, using raw JavaScript injection to bypass `readonly` fields.
- **Zero-Trace Data Wiping**: Clicking "Wipe" permanently zeros-out and deletes all local JSON session files.

---

## ⚙️ Prerequisites
Running a 31B parameter Vision model locally natively in `bfloat16` is highly resource-intensive. Ensure your system meets the following specifications:

**Hardware Requirements (AI Backend):**
- **GPU**: At least ~64GB+ of total VRAM is required to load the model weights and KV cache.
  - *Recommended Setup*: 2x NVIDIA RTX A6000 (48GB), or 1x NVIDIA A100/H100 (80GB).
  - *Alternative Setup*: Mac Studio with M2/M3 Ultra (128GB+ Unified Memory) using MLX/vLLM Apple Silicon support (requires configuration adjustments).
- **RAM**: 64GB+ System RAM.
- **Storage**: 100GB+ fast NVMe SSD storage for model weights.

**Software Requirements:**
- **OS**: Linux (Ubuntu 20.04/22.04 recommended) or macOS (Apple Silicon).
- **Python**: 3.11 (managed via Conda)
- **Conda**: Anaconda or Miniconda
- **CUDA**: CUDA Toolkit 12.1+ (if using NVIDIA GPUs).

---

## 🚀 Installation & Setup

### 0. Create Conda Environment
We recommend using Conda with Python 3.11 to manage frontend and backend all dependencies cleanly, you can create 1 for each if needed:
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

3. **Configure Credentials**
   Create `config.json`:
   ```json
   {
       "ntu_username": "YOUR_NTU_ID",
       "ntu_password": "YOUR_NTU_PASSWORD",
       "agent_base_url": "YOUR_AGENT_URL_IF_NEEDED",
       "project_code": "114L3073",
       "expense_type": "教材費(書籍)"
   }
   ```

3. **Start the AI Vision Backend**
   Launch the vLLM server:
   ```bash
   ./vllm.sh
   ```
   *Wait for the model weights to load into VRAM.* Then start the extraction API:
   ```bash
   python api.py
   ```

4. **Start the Web UI**
   ```bash
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
1. Open `http://localhost:8001`.
2. **Step 1**: Enter the Payee Information (ID, Bank Code, Account Number).
3. **Step 2**: Click **"📷 使用電腦相機拍照"** to snap a photo of your receipt using a document camera.
4. **Step 3**: The local Gemma backend processes the image. Review and confirm the extracted data.
5. **Step 4**: Click **Execute**. The Playwright agent will automatically log into the NTU portal and submit everything.
6. **Step 5**: Print your physical copies and click **Wipe Data** to securely erase your session.
