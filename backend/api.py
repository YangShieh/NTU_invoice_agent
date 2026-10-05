from fastapi import FastAPI, File, Header, UploadFile
import uvicorn
import os
import json
import base64
import tempfile
import time
from openai import OpenAI
from gemma import scan_taiwan_einvoice_qr, clean_json_output, validate_and_clean_data, PROMPTS
from diagnostics import error_details, log_event

app = FastAPI(title="Invoice Extraction API")

# Initialize OpenAI client pointing to the local vLLM server
# The API key can be anything when querying a local vLLM instance
VLLM_BASE_URL = os.getenv("VLLM_BASE_URL", "http://localhost:8080/v1")
MODEL_NAME = os.getenv("MODEL_NAME", "mattbucci/gemma-4-12B-AWQ")
vllm_client = OpenAI(base_url=VLLM_BASE_URL, api_key="vllm-local")

def encode_image_to_base64(image_path):
    """Convert local image to base64 for the vLLM vision payload."""
    with open(image_path, "rb") as image_file:
        return base64.b64encode(image_file.read()).decode('utf-8')

def classify_invoice_vllm(base64_image):
    """Use vLLM to classify the invoice type."""
    prompt = "請觀察這張發票特徵，並嚴格分類為: 'einvoice' (電子發票), 'old' (傳統印刷發票) 或 'hand' (手寫收據)。只輸出代號。"
    
    response = vllm_client.chat.completions.create(
        model=MODEL_NAME,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}
                    }
                ],
            }
        ],
        max_tokens=10,
        temperature=0.0
    )
    
    output_text = response.choices[0].message.content.strip().lower()
    if "einvoice" in output_text: return "einvoice"
    elif "old" in output_text: return "old"
    elif "hand" in output_text: return "hand"
    return "einvoice"

def extract_invoice_vllm(base64_image, prompt):
    """Extract full JSON data using vLLM."""
    response = vllm_client.chat.completions.create(
        model=MODEL_NAME,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}
                    }
                ],
            }
        ],
        max_tokens=1500,
        temperature=0.0
    )
    return response.choices[0].message.content

@app.post("/extract")
async def extract_invoice(
    file: UploadFile = File(...), x_case_id: str = Header(default="unassigned")
):
    started = time.monotonic()
    suffix = os.path.splitext(file.filename or "upload.jpg")[1][:10] or ".jpg"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as buffer:
        temp_path = buffer.name
        while chunk := await file.read(1024 * 1024):
            buffer.write(chunk)
    log_event(
        x_case_id, "ocr.backend", "started",
        content_type=file.content_type or "unknown",
        size_bytes=os.path.getsize(temp_path),
    )
    try:
        # Encode image once for all vLLM calls
        base64_image = encode_image_to_base64(temp_path)

        # 1. Classification & QR scan
        invoice_type = classify_invoice_vllm(base64_image)
        log_event(x_case_id, "ocr.classification", "completed", detected_type=invoice_type)
        qr_data = None
        if invoice_type == "einvoice":
            qr_data = scan_taiwan_einvoice_qr(temp_path)
            log_event(
                x_case_id, "ocr.qr", "completed" if qr_data else "not_found",
                qr_verified=bool(qr_data),
            )
            
        # 2. VLM Deep Extraction via vLLM
        task_prompt = PROMPTS[invoice_type]
        raw_response = extract_invoice_vllm(base64_image, task_prompt)
        clean_text = clean_json_output(raw_response)
        
        # 3. JSON parsing and data merging
        invoice_data = json.loads(clean_text)
        invoice_data["detected_type"] = invoice_type
        
        if qr_data:
            invoice_data["invoice_number"] = qr_data["invoice_number"]
            invoice_data["date"] = qr_data["date"]
            invoice_data["total_amount"] = qr_data["total_amount"]
            invoice_data["qr_verified"] = True
        else:
            invoice_data["qr_verified"] = False

        invoice_data = validate_and_clean_data(invoice_data)
        log_event(
            x_case_id, "ocr.backend", "completed",
            duration_ms=round((time.monotonic() - started) * 1000),
            detected_type=invoice_type,
            qr_verified=bool(qr_data),
            item_count=len(invoice_data.get("items", [])),
        )
        return invoice_data

    except Exception as e:
        log_event(
            x_case_id, "ocr.backend", "failed",
            duration_ms=round((time.monotonic() - started) * 1000),
            **error_details(e),
        )
        return {"error": str(e)}
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)

if __name__ == "__main__":
    # FastAPI runs on 8000, calling vLLM on 8080
    uvicorn.run(app, host="0.0.0.0", port=8000)
