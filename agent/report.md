# NTU Invoice Agent: Comprehensive System Report

## 1. Executive Summary
The NTU Invoice Agent is a highly specialized automation suite designed to digitize, parse, and automate the submission of university expense claims. By fusing a responsive web frontend, a localized cutting-edge AI Vision pipeline, and a headless Playwright agent, the system guarantees privacy, drastically reduces data entry time, and mitigates the risk of human error inherent in legacy ASP-based systems.

---

## 2. System Architecture & Data Flow

The architecture operates via three distinct layers communicating asynchronously:

### Data Flow Overview:
1. **Input & Capture (Frontend)**: User inputs payee details or captures invoice images using an integrated Document Camera via the Web UI (`session_ui.py`).
2. **Vision Processing (Backend)**: The image is sent to `api.py`, which leverages a local instance of `google/gemma-4-31b-it` (running via `vllm.sh`) to classify and extract JSON data. QR codes are parsed conventionally via `gemma.py` to guarantee deterministic accuracy.
3. **Validation**: User reviews the parsed OCR data in the UI. Confirmed payloads are serialized to `session.json`.
4. **Execution**: `agent.py` consumes `session.json` and `config.json`, launching a Chromium instance to inject the structured data into the NTU accounting portal.
5. **Sanitization**: A final wipe securely zeroes out all local JSON and temporary files, shutting down the automation context.

---

## 3. Component Deep Dive

### 3.1 Advanced AI Vision Backend (`vllm.sh`, `api.py`, `gemma.py`)
To prevent data leakage to external cloud APIs, the system hosts its own state-of-the-art vision extraction pipeline natively on-premise.

**Hardware & Resource Profile:**
Running a 31-Billion parameter Vision model locally in unquantized `bfloat16` precision is a highly demanding workload. It requires:
- **VRAM**: Approximately 64GB+ to load the weights and manage the KV cache for the 16K context window.
- **Compute**: Ideally executed across dual heavy-compute GPUs (e.g., 2x NVIDIA RTX A6000s) or a high-unified-memory architecture (e.g., Mac Studio M2/M3 Ultra with 128GB+ memory).

- **vLLM Engine (`vllm.sh`)**: Runs the massive `google/gemma-4-31b-it` model utilizing highly optimized `TORCH_SDPA` attention backends and `bfloat16` precision. It provides an OpenAI-compatible API on `localhost:8000`.
- **Intelligent Routing Pipeline (`api.py`)**: 
  - **Classification**: Prompts the LLM to strictly classify the invoice as an `einvoice` (電子發票), `old` (傳統發票), or `hand` (手寫收據).
  - **Hybrid Extraction**: If the invoice is an `einvoice`, `gemma.py` invokes a deterministic QR decoder to extract the invoice number, date, and exact total amount. This guarantees that critical financial data suffers zero LLM hallucination.
  - **VLM Deep Extraction**: The image is re-fed into the LLM with a context-specific prompt (based on the classification) to extract individual line items, descriptions, and quantities.
  - **Merging**: The deterministic QR data and the probabilistic LLM line items are merged, sanitized (`clean_json_output`), and returned to the frontend.

### 3.2 FastAPI Web Orchestrator (`session_ui.py`)
Acts as the user-facing bridge between the complex LLM backend and the Playwright executor.
- **Hardware Integration (WebRTC)**: Utilizes `navigator.mediaDevices.enumerateDevices()` to scan for multiple video inputs, allowing users to hot-swap between a built-in laptop webcam and an external USB Document Camera dynamically.
- **Data Sanitization**: Pydantic models validate data structures. Complex inputs like Bank Names are constrained to strict Dropdowns (e.g., 008, 808) to prevent backend validation errors.

### 3.3 Playwright Automation Agent (`agent.py`)
The execution engine built for resilience against an unstable, frame-heavy legacy DOM.
- **Security Bypasses**: The NTU login utilizes legacy JS events. The agent executes `page.evaluate("if(typeof change === 'function') change();")` to forcibly trigger hidden UI elements.
- **Multi-Frame Navigation**: Iteratively hunts through `<frame>` arrays rather than relying on brittle static indexes.
- **Dynamic Payee Registration**: 
  - Automatically spawns new tabs if Payee `MID` is missing.
  - Uses highly robust, image-based CSS locators (`img[src*='insert.gif']`) to locate poorly-labeled action buttons.
  - Bypasses `readonly=""` HTML constraints on Bank Codes by executing raw JavaScript (`node => node.value = '008'`), sidestepping fragile popup-window selection flows entirely.
- **Encoding Truncation**: Strictly enforces a 50-byte limit on the "Purpose" field, calculated via Big5 encoding (where Chinese characters count as 2 bytes), preventing server-side crashes during submission.

### 3.4 Core Python Dependencies
The system minimizes external reliance by strictly isolating its functional domains into two distinct dependency groups:

#### A. Frontend & Automation Agent (`session_ui.py`, `agent.py`)
- **`fastapi` & `uvicorn`**: High-performance asynchronous web server orchestrating the user interface.
- **`playwright`**: Used for the execution engine instead of Selenium due to its superior frame-handling, asynchronous architecture, and ability to execute raw JavaScript bypassing headless detection.
- **`pydantic` & `python-multipart`**: Enforces strict typing (e.g., payee bank code validation) and safely parses raw binary `FormData` containing the base64/JPEG webcam captures.

#### B. AI Vision Backend (`vllm.sh`, `api.py`, `gemma.py`)
- **`vllm` & `torch`**: Chosen for its exceptional inference speed and PagedAttention memory management, which is critical for loading the 31B Gemma model into GPU VRAM.
- **`opencv-python` (`cv2`) & `Pillow` (`PIL`)**: Handles image ingestion, resizing, and matrix manipulation before feeding tensors to the model or QR scanner.
- **`pyzbar` & `qreader`**: Advanced computer-vision packages specifically utilized by `gemma.py` to reliably decode the highly-dense, specialized QR codes found on Taiwan e-invoices, completely bypassing the LLM for deterministic data.
- **`opencc`**: Automatically performs Simplified to Traditional Chinese text conversion on the LLM's output to ensure maximum compatibility with the legacy NTU accounting database (Big5).
- **`openai`**: Acts as a lightweight SDK to communicate with the locally-hosted `vLLM` server using standard chat-completion formats.

---

## 4. Security & Privacy Posture
The system enforces a strict zero-trust local execution environment.
- **Zero Cloud Footprint**: Both the AI Vision extraction and browser automation happen 100% locally on the host machine.
- **Cryptographic Wiping**: The `/api/wipe` endpoint employs a secure overwrite mechanism (`f.write(b"\x00" * size)`) before filesystem deletion. This guarantees that highly sensitive data (ID numbers, bank accounts) cannot be trivially recovered via forensic analysis once the session ends.

---

## 5. Conclusion
The NTU Invoice Agent represents a textbook example of modernizing legacy infrastructure via side-channel automation. By leveraging localized LLMs for intelligent parsing and Playwright for deterministic execution, the system achieves a highly fault-tolerant, fully private workflow that drastically outperforms manual data entry.
