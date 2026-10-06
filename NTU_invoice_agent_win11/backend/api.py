from fastapi import FastAPI, File, Header, UploadFile
import uvicorn
import os
import json
import base64
import tempfile
import time
from io import BytesIO

from openai import OpenAI
from PIL import Image, ImageOps

from gemma import (
    scan_taiwan_einvoice_qr,
    clean_json_output,
    validate_and_clean_data,
    PROMPTS,
)
from diagnostics import error_details, log_event


app = FastAPI(title="Invoice Extraction API")


# Initialize OpenAI client pointing to the local vLLM server.
# The API key can be anything when querying a local vLLM instance.
VLLM_BASE_URL = os.getenv(
    "VLLM_BASE_URL",
    "http://localhost:8080/v1",
)

MODEL_NAME = os.getenv(
    "MODEL_NAME",
    "mattbucci/gemma-4-12B-AWQ",
)

vllm_client = OpenAI(
    base_url=VLLM_BASE_URL,
    api_key="vllm-local",
)


def encode_image_to_base64(
    image_path,
    canvas_size=1024,
):
    """
    Resize proportionally and pad an image to a square white canvas
    before sending it to Gemma 4.

    The original image remains unchanged and is still used for
    QR-code recognition.
    """
    with Image.open(image_path) as source:
        image = ImageOps.exif_transpose(source).convert("RGB")

        original_width, original_height = image.size

        # Preserve the complete receipt:
        # do not crop or stretch it.
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

        canvas.paste(
            image,
            (paste_x, paste_y),
        )

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

    return base64.b64encode(
        output.getvalue()
    ).decode("utf-8")


def classify_invoice_vllm(base64_image):
    """Use vLLM to classify the invoice type."""
    prompt = (
        "請觀察這張發票特徵，並嚴格分類為: "
        "'einvoice' (電子發票), "
        "'old' (傳統印刷發票) 或 "
        "'hand' (手寫收據)。"
        "只輸出代號。"
    )

    response = vllm_client.chat.completions.create(
        model=MODEL_NAME,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": prompt,
                    },
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": (
                                "data:image/jpeg;base64,"
                                f"{base64_image}"
                            )
                        },
                    },
                ],
            }
        ],
        max_tokens=10,
        temperature=0.0,
    )

    output_text = (
        response
        .choices[0]
        .message
        .content
        .strip()
        .lower()
    )

    if "einvoice" in output_text:
        return "einvoice"

    if "old" in output_text:
        return "old"

    if "hand" in output_text:
        return "hand"

    # Default to electronic invoice if classification is unclear.
    return "einvoice"


def extract_invoice_vllm(
    base64_image,
    prompt,
):
    """Extract full invoice JSON data using vLLM."""
    response = vllm_client.chat.completions.create(
        model=MODEL_NAME,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": prompt,
                    },
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": (
                                "data:image/jpeg;base64,"
                                f"{base64_image}"
                            )
                        },
                    },
                ],
            }
        ],
        max_tokens=1500,
        temperature=0.0,
    )

    return response.choices[0].message.content


@app.post("/extract")
async def extract_invoice(
    file: UploadFile = File(...),
    x_case_id: str = Header(default="unassigned"),
):
    started = time.monotonic()
    temp_path = None

    try:
        # Keep only a short file extension.
        suffix = (
            os.path.splitext(
                file.filename or "upload.jpg"
            )[1][:10]
            or ".jpg"
        )

        # Use a unique temporary filename to prevent filename conflicts.
        with tempfile.NamedTemporaryFile(
            delete=False,
            suffix=suffix,
        ) as buffer:
            temp_path = buffer.name

            # Copy the upload in 1 MB chunks.
            while chunk := await file.read(
                1024 * 1024
            ):
                buffer.write(chunk)

        log_event(
            x_case_id,
            "ocr.backend",
            "started",
            content_type=(
                file.content_type or "unknown"
            ),
            size_bytes=os.path.getsize(temp_path),
        )

        # Normalize and encode the image once for all vLLM calls.
        base64_image = encode_image_to_base64(
            temp_path
        )

        # 1. Classify the invoice.
        invoice_type = classify_invoice_vllm(
            base64_image
        )

        log_event(
            x_case_id,
            "ocr.classification",
            "completed",
            detected_type=invoice_type,
        )

        # 2. Scan the original image for a Taiwan e-invoice QR code.
        qr_data = None

        if invoice_type == "einvoice":
            qr_data = scan_taiwan_einvoice_qr(
                temp_path
            )

            log_event(
                x_case_id,
                "ocr.qr",
                (
                    "completed"
                    if qr_data
                    else "not_found"
                ),
                qr_verified=bool(qr_data),
            )

        # 3. Extract invoice fields with the VLM.
        task_prompt = PROMPTS[invoice_type]

        raw_response = extract_invoice_vllm(
            base64_image,
            task_prompt,
        )

        clean_text = clean_json_output(
            raw_response
        )

        # 4. Parse and merge the extracted information.
        invoice_data = json.loads(clean_text)

        invoice_data["detected_type"] = (
            invoice_type
        )

        if qr_data:
            invoice_data["invoice_number"] = (
                qr_data["invoice_number"]
            )
            invoice_data["date"] = (
                qr_data["date"]
            )
            invoice_data["total_amount"] = (
                qr_data["total_amount"]
            )
            invoice_data["qr_verified"] = True
        else:
            invoice_data["qr_verified"] = False

        invoice_data = validate_and_clean_data(
            invoice_data
        )

        log_event(
            x_case_id,
            "ocr.backend",
            "completed",
            duration_ms=round(
                (
                    time.monotonic() - started
                )
                * 1000
            ),
            detected_type=invoice_type,
            qr_verified=bool(qr_data),
            item_count=len(
                invoice_data.get("items", [])
            ),
        )

        return invoice_data

    except Exception as error:
        log_event(
            x_case_id,
            "ocr.backend",
            "failed",
            duration_ms=round(
                (
                    time.monotonic() - started
                )
                * 1000
            ),
            **error_details(error),
        )

        return {
            "error": str(error)
        }

    finally:
        # Always remove the temporary upload,
        # whether processing succeeds or fails.
        if (
            temp_path
            and os.path.exists(temp_path)
        ):
            os.remove(temp_path)


if __name__ == "__main__":
    # FastAPI runs on port 8000 and calls vLLM on port 8080.
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8000,
    )