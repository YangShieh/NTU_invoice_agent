from fastapi import FastAPI, File, UploadFile
import uvicorn
import shutil
import os
import json
import base64
from openai import OpenAI
from gemma import scan_taiwan_einvoice_qr, clean_json_output, validate_and_clean_data, PROMPTS

app = FastAPI(title="Invoice Extraction API")

# Initialize OpenAI client pointing to the local vLLM server
# The API key can be anything when querying a local vLLM instance
vllm_client = OpenAI(
    base_url="http://localhost:8080/v1",
    api_key="vllm-local"
)

# Replace with the exact model string used in your vLLM startup command
MODEL_NAME = "mattbucci/gemma-4-12B-AWQ"

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
async def extract_invoice(file: UploadFile = File(...)):
    temp_path = f"temp_{file.filename}"
    with open(temp_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
    
    try:
        # Encode image once for all vLLM calls
        base64_image = encode_image_to_base64(temp_path)

        # 1. Classification & QR scan
        invoice_type = classify_invoice_vllm(base64_image)
        qr_data = None
        if invoice_type == "einvoice":
            qr_data = scan_taiwan_einvoice_qr(temp_path)
            
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
        
        # Clean up temp file
        os.remove(temp_path)
        return invoice_data

    except Exception as e:
        if os.path.exists(temp_path):
            os.remove(temp_path)
        return {"error": str(e)}

if __name__ == "__main__":
    # FastAPI runs on 8000, calling vLLM on 8080
    uvicorn.run(app, host="0.0.0.0", port=8000)