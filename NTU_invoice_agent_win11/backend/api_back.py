from fastapi import FastAPI, File, UploadFile
import uvicorn
import shutil
import os
import json
import base64
from io import BytesIO
from openai import OpenAI
from PIL import Image, ImageOps
from gemma import scan_taiwan_einvoice_qr, clean_json_output, validate_and_clean_data, PROMPTS

app = FastAPI(title="Invoice Extraction API")

# Initialize OpenAI client pointing to the local vLLM server
# The API key can be anything when querying a local vLLM instance
VLLM_BASE_URL = os.getenv("VLLM_BASE_URL", "http://localhost:8080/v1")
MODEL_NAME = os.getenv("MODEL_NAME", "mattbucci/gemma-4-12B-AWQ")
vllm_client = OpenAI(base_url=VLLM_BASE_URL, api_key="vllm-local")

def encode_image_to_base64(image_path, canvas_size=1024):
    """
    Resize proportionally and pad an image to a square white canvas before
    sending it to Gemma 4. The original image remains unchanged and is still
    used for QR-code recognition.
    """
    with Image.open(image_path) as source:
        image = ImageOps.exif_transpose(source).convert("RGB")
        original_width, original_height = image.size

        # Preserve the complete receipt: do not crop or stretch it.
        image.thumbnail(
            (canvas_size, canvas_size),
            Image.Resampling.LANCZOS,
        )

        canvas = Image.new(
            "RGB",
            (canvas_size, canvas_size),
            color=(255, 255, 255),
        )

        paste_x = (canvas_size - image.width) // 2
        paste_y = (canvas_size - image.height) // 2
        canvas.paste(image, (paste_x, paste_y))

        output = BytesIO()
        canvas.save(
            output,
            format="JPEG",
            quality=92,
            optimize=True,
        )

    print(
        "Gemma 4 image normalization: "
        f"{original_width}x{original_height} -> "
        f"{canvas_size}x{canvas_size}"
    )

    return base64.b64encode(output.getvalue()).decode("utf-8")

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
