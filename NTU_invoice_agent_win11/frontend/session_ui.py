"""
Merged Session UI — working backup plus the new diagnostics, editing, retry,
session-restoration, navigation, and inline-print features.
Flow:
  1. User fills in their own payee info
  2. User uploads / captures invoice image
  3. OCR runs, user reviews and edits the result
  4. User confirms → agent submits to NTU
  5. User confirms completion → ALL personal data wiped from disk

Run: python session_ui.py
Open: http://localhost:8001
"""

from fastapi import FastAPI, File, UploadFile, HTTPException
from fastapi.responses import HTMLResponse, Response
import inspect
import uvicorn, json, os, shutil, requests, time, subprocess
from pydantic import BaseModel
from diagnostics import error_details, log_event, new_case_id

SESSION_FILE = "session.json"   # wiped after confirmation
TEMP_IMAGE   = "session_image.jpg"
CONFIG_FILE  = "config.json"

app = FastAPI()

@app.get("/favicon.ico")
def favicon():
    svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512"><defs><linearGradient id="bg" x1="0" y1="0" x2="1" y2="1"><stop offset="0%" stop-color="#1a237e"/><stop offset="100%" stop-color="#00897b"/></linearGradient></defs><rect width="512" height="512" rx="80" fill="url(#bg)"/><text x="256" y="300" text-anchor="middle" font-size="200" font-family="serif" fill="white" font-weight="bold">希望</text></svg>'
    return Response(content=svg, media_type="image/svg+xml")

@app.get("/icon-512.svg")
def app_icon():
    svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512"><defs><linearGradient id="bg" x1="0" y1="0" x2="1" y2="1"><stop offset="0%" stop-color="#1a237e"/><stop offset="100%" stop-color="#00897b"/></linearGradient></defs><rect width="512" height="512" rx="80" fill="url(#bg)"/><text x="256" y="300" text-anchor="middle" font-size="200" font-family="serif" fill="white" font-weight="bold">希望</text></svg>'
    return Response(content=svg, media_type="image/svg+xml")

@app.get("/manifest.json")
def manifest():
    m = {
        "name": "希望 - 報帳助理",
        "short_name": "希望",
        "description": "NTU 智慧報帳系統",
        "start_url": "/invoice_wish",
        "scope": "/",
        "display": "standalone",
        "background_color": "#f5f5f0",
        "theme_color": "#1a237e",
        "orientation": "portrait",
        "icons": [
            {"src": "/icon-512.svg", "sizes": "512x512", "type": "image/svg+xml", "purpose": "any maskable"}
        ]
    }
    return Response(content=json.dumps(m), media_type="application/manifest+json")

@app.get("/sw.js")
def service_worker():
    """Minimal service worker to make the PWA installable."""
    sw_code = """
self.addEventListener('install', e => { self.skipWaiting(); });
self.addEventListener('activate', e => { e.waitUntil(clients.claim()); });
self.addEventListener('fetch', e => { e.respondWith(fetch(e.request)); });
    """
    return Response(content=sw_code.strip(), media_type="application/javascript")

def load_config() -> dict:
    with open(CONFIG_FILE, encoding="utf-8") as f:
        return json.load(f)

def load_session() -> dict:
    if not os.path.exists(SESSION_FILE):
        return {}
    with open(SESSION_FILE, encoding="utf-8") as f:
        return json.load(f)

def save_session(data: dict):
    with open(SESSION_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def ensure_case_id(session: dict) -> str:
    case_id = session.get("case_id")
    if not case_id:
        case_id = new_case_id()
        session["case_id"] = case_id
    return case_id

def wipe_session():
    """Remove all personal data from disk."""
    for path in [SESSION_FILE, TEMP_IMAGE, "print_page.pdf"]:
        if os.path.exists(path):
            # Overwrite with zeros before deleting (basic privacy)
            with open(path, "wb") as f:
                f.write(b"\x00" * os.path.getsize(path))
            os.remove(path)

# ── API endpoints ─────────────────────────────────────────────────────────────

class PayeeInfo(BaseModel):
    payee_id: str
    payee_name: str
    bank_code: str
    account_number: str


class InvoiceData(BaseModel):
    invoice_number: str
    date: str
    total_amount: str
    items: list
    expense_purpose: str

@app.post("/api/payee")
def save_payee(p: PayeeInfo):
    session = load_session()
    case_id = ensure_case_id(session)
    session["payee"] = p.model_dump()
    # Editing personal data must not discard already-confirmed invoices.
    session["step"] = "invoice_confirmed" if session.get("invoices") else "payee_saved"
    save_session(session)
    log_event(case_id, "session.payee", "completed")
    return {"ok": True}

@app.post("/api/upload")
async def upload_image(file: UploadFile = File(...)):
    session = load_session()
    case_id = ensure_case_id(session)
    save_session(session)
    started = time.monotonic()
    # Save image to temp path
    with open(TEMP_IMAGE, "wb") as buf:
        shutil.copyfileobj(file.file, buf)
    log_event(
        case_id,
        "ocr.upload",
        "started",
        content_type=file.content_type or "unknown",
        size_bytes=os.path.getsize(TEMP_IMAGE),
    )
    # Run OCR
    cfg = load_config()
    try:
        with open(TEMP_IMAGE, "rb") as f:
            resp = requests.post(
                f"{cfg['ocr_base_url']}/extract",
                files={"file": (file.filename, f, "image/jpeg")},
                headers={"X-Case-ID": case_id},
                timeout=120,
            )
        resp.raise_for_status()
        invoice = resp.json()
    except Exception as e:
        log_event(
            case_id, "ocr.request", "failed",
            duration_ms=round((time.monotonic() - started) * 1000),
            **error_details(e),
        )
        raise HTTPException(500, f"OCR failed: {e}（案件 ID: {case_id}）")

    if "error" in invoice:
        log_event(
            case_id, "ocr.extraction", "failed",
            duration_ms=round((time.monotonic() - started) * 1000),
            backend_error=invoice["error"],
        )
        raise HTTPException(500, f"{invoice['error']}（案件 ID: {case_id}）")

    # Build default expense_purpose
    items = invoice.get("items", [])
    cfg = load_config()
    if items:
        invoice["expense_purpose"] = f"{cfg['expense_type']}_{items[0]['name']}"
    else:
        invoice["expense_purpose"] = cfg["expense_type"]

    session["invoice"] = invoice
    session["step"] = "ocr_done"
    save_session(session)
    required = ("invoice_number", "date", "total_amount", "items")
    log_event(
        case_id, "ocr.extraction", "completed",
        duration_ms=round((time.monotonic() - started) * 1000),
        detected_type=invoice.get("detected_type", "unknown"),
        qr_verified=bool(invoice.get("qr_verified")),
        confidence_score=invoice.get("confidence_score"),
        missing_fields=[key for key in required if not invoice.get(key)],
        item_count=len(items),
    )
    return invoice

@app.post("/api/confirm-invoice")
def confirm_invoice(data: InvoiceData):
    """User has reviewed/edited the OCR data — save final version."""
    session = load_session()
    case_id = ensure_case_id(session)
    original = session.get("invoice", {})
    submitted = data.model_dump()
    changed_fields = [
        key for key in ("invoice_number", "date", "total_amount", "items", "expense_purpose")
        if original.get(key) != submitted.get(key)
    ]
    if "invoices" not in session:
        session["invoices"] = []
    session["invoices"].append(submitted)
    session["step"] = "invoice_confirmed"
    save_session(session)
    log_event(
        case_id, "invoice.review", "completed",
        changed_fields=changed_fields,
        correction_count=len(changed_fields),
    )
    return {"ok": True, "count": len(session["invoices"])}

@app.get("/api/invoices")
def list_invoices():
    session = load_session()
    return {"invoices": session.get("invoices", [])}

@app.put("/api/invoices/{invoice_index}")
def update_invoice(invoice_index: int, data: InvoiceData):
    session = load_session()
    case_id = ensure_case_id(session)
    invoices = session.get("invoices", [])
    if invoice_index < 0 or invoice_index >= len(invoices):
        raise HTTPException(404, f"找不到指定發票（案件 ID: {case_id}）")
    old = invoices[invoice_index]
    updated = data.model_dump()
    changed_fields = [key for key in updated if old.get(key) != updated.get(key)]
    invoices[invoice_index] = updated
    session["invoices"] = invoices
    session["invoice"] = updated
    session["step"] = "invoice_confirmed"
    save_session(session)
    log_event(
        case_id, "invoice.edit", "completed", invoice_index=invoice_index,
        changed_fields=changed_fields, correction_count=len(changed_fields),
    )
    return {"ok": True, "count": len(invoices)}

@app.post("/api/open-browser")
def open_browser_endpoint():
    """Step A — open browser to NTU login page for the user to log in manually."""
    from agent import open_browser_for_login
    session = load_session()
    case_id = ensure_case_id(session)
    save_session(session)
    try:
        open_browser_for_login()
        log_event(case_id, "browser.open", "completed")
        return {"ok": True}
    except Exception as e:
        log_event(case_id, "browser.open", "failed", **error_details(e))
        raise HTTPException(500, f"{e}（案件 ID: {case_id}）")

@app.post("/api/run-agent")
def run_agent_endpoint(manual: bool = False):
    """Step B — called after the user confirms they've logged in. Runs the agent loop."""
    session = load_session()
    case_id = ensure_case_id(session)
    if "payee" not in session or "invoices" not in session or not session["invoices"]:
        log_event(case_id, "agent.validation", "failed", reason="missing_payee_or_invoices")
        raise HTTPException(400, f"Missing payee or invoices data in session（案件 ID: {case_id}）")
    if session.get("step") != "invoice_confirmed":
        log_event(case_id, "agent.validation", "failed", reason="invoice_not_confirmed")
        raise HTTPException(400, f"Invoice not confirmed yet（案件 ID: {case_id}）")

    cfg = load_config()
    from agent import close_browser, run_submission, _state
    started = time.monotonic()
    max_attempts = 1 if manual else 3
    log_event(
        case_id, "agent.run", "started", invoice_count=len(session["invoices"]),
        max_attempts=max_attempts, manual_retry=manual,
    )
    report_number = ""
    last_error = None
    completed_attempt = 0
    for attempt in range(1, max_attempts + 1):
        attempt_started = time.monotonic()
        log_event(case_id, "agent.attempt", "started", attempt=attempt, max_attempts=max_attempts)
        try:
            run_parameters = inspect.signature(run_submission).parameters
            supports_case_id = (
                "case_id" in run_parameters
                or any(
                    parameter.kind == inspect.Parameter.VAR_KEYWORD
                    for parameter in run_parameters.values()
                )
            )
            run_kwargs = {"headless": False}
            if supports_case_id:
                run_kwargs["case_id"] = case_id

            report_number = run_submission(
                cfg,
                session["payee"],
                session["invoices"],
                **run_kwargs,
            )
            report_number = str(
                report_number or _state.get("report_number") or ""
            ).strip()
            if not report_number:
                last_tool = _state.get("last_tool") or "unknown"
                last_outcome = _state.get("last_tool_outcome") or "unknown"
                raise RuntimeError(
                    "Agent stopped before NTU produced a report number. "
                    f"Last step: {last_tool} ({last_outcome})"
                )
            completed_attempt = attempt
            log_event(
                case_id, "agent.attempt", "completed", attempt=attempt,
                duration_ms=round((time.monotonic() - attempt_started) * 1000),
            )
            break
        except Exception as exc:
            captured_number = str(_state.get("report_number") or "").strip()
            if captured_number:
                report_number = captured_number
                completed_attempt = attempt
                log_event(
                    case_id, "agent.attempt", "completed", attempt=attempt,
                    duration_ms=round((time.monotonic() - attempt_started) * 1000),
                    recovery="captured_ntu_asn_after_agent_error",
                )
                try:
                    close_browser()
                except Exception:
                    pass
                break

            last_error = exc
            log_event(
                case_id, "agent.attempt", "failed", attempt=attempt,
                duration_ms=round((time.monotonic() - attempt_started) * 1000),
                **error_details(exc),
            )
            try:
                close_browser()
            except Exception:
                pass
            if attempt < max_attempts:
                time.sleep(min(2 ** (attempt - 1), 4))

    try:
        if not report_number:
            raise last_error or RuntimeError("Agent failed")
        session["step"] = "agent_done"
        session["report_number"] = report_number
        save_session(session)
        print_url = _state.get("print_url", "")
        log_event(
            case_id, "agent.run", "completed",
            duration_ms=round((time.monotonic() - started) * 1000),
            print_url_available=bool(print_url),
            completed_attempt=completed_attempt,
        )
        return {
            "ok": True, "report_number": report_number, "print_url": print_url,
            "attempts": completed_attempt,
        }
    except Exception as e:
        log_event(
            case_id, "agent.run", "failed",
            duration_ms=round((time.monotonic() - started) * 1000),
            **error_details(e),
        )
        raise HTTPException(500, f"{e}（案件 ID: {case_id}）")

@app.post("/api/close-browser")
def close_browser_endpoint():
    from agent import close_browser
    close_browser()
    return {"ok": True}

@app.get("/api/print-page")
def get_print_page():
    """Serve the captured PDF of the print page."""
    from fastapi.responses import FileResponse
    pdf_path = os.path.join(os.path.dirname(__file__), "print_page.pdf")
    case_id = load_session().get("case_id", "unassigned")
    if os.path.exists(pdf_path):
        log_event(case_id, "print.serve", "completed", size_bytes=os.path.getsize(pdf_path))
        return FileResponse(
            pdf_path,
            media_type="application/pdf",
            filename="黏存單.pdf",
            content_disposition_type="inline",
        )
    log_event(case_id, "print.serve", "failed", reason="pdf_not_found")
    raise HTTPException(404, "尚未產生列印頁面")

@app.post("/api/wipe")
def wipe_endpoint():
    """User confirms completion — wipe all personal data."""
    case_id = load_session().get("case_id", "unassigned")
    log_event(case_id, "session.wipe", "completed")
    wipe_session()
    from agent import close_browser
    try:
        close_browser()
    except Exception:
        pass
    return {"ok": True, "message": "All personal data has been removed."}

@app.get("/api/session")
def get_session():
    s = load_session()
    # Never send raw payee banking details back to the browser unnecessarily
    safe = {k: v for k, v in s.items() if k != "payee"}
    if "payee" in s:
        safe["payee"] = {
            "payee_name": s["payee"].get("payee_name"),
            "payee_id_hint": s["payee"].get("payee_id", "")[:3] + "******",
        }
    return safe

# ── Single-page UI ────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
def landing():
    return r"""<!DOCTYPE html>
<html lang="zh-TW">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-title" content="希望">
<meta name="theme-color" content="#1a237e">
<link rel="manifest" href="/manifest.json">
<link rel="apple-touch-icon" href="/icon-512.svg">
<title>希望</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:system-ui,sans-serif;display:flex;align-items:center;justify-content:center;min-height:100vh;background:linear-gradient(135deg,#1a237e 0%,#00897b 100%)}
.landing{text-align:center;animation:fadeIn .8s ease}
.icon-btn{display:inline-flex;align-items:center;justify-content:center;width:180px;height:180px;border-radius:40px;background:rgba(255,255,255,0.15);backdrop-filter:blur(20px);border:2px solid rgba(255,255,255,0.3);cursor:pointer;transition:all .3s ease;text-decoration:none;box-shadow:0 20px 60px rgba(0,0,0,0.3)}
.icon-btn:hover{transform:scale(1.08);background:rgba(255,255,255,0.25);box-shadow:0 25px 70px rgba(0,0,0,0.4)}
.icon-btn:active{transform:scale(0.96)}
.icon-text{font-size:64px;font-weight:bold;color:white;font-family:serif}
.label{color:rgba(255,255,255,0.9);font-size:16px;margin-top:24px;font-weight:500;letter-spacing:2px}
.sublabel{color:rgba(255,255,255,0.5);font-size:12px;margin-top:8px}
@keyframes fadeIn{from{opacity:0;transform:translateY(20px)}to{opacity:1;transform:translateY(0)}}
</style>
</head>
<body>
<div class="landing">
  <a href="/invoice_wish" class="icon-btn"><span class="icon-text">希望</span></a>
  <p class="label">NTU 報帳助理</p>
  <p class="sublabel">點擊開始</p>
  <button id="install-btn" style="display:none;margin-top:20px;padding:10px 24px;border:2px solid rgba(255,255,255,0.5);border-radius:20px;background:rgba(255,255,255,0.15);color:white;font-size:14px;cursor:pointer;backdrop-filter:blur(10px)">📲 安裝應用程式</button>
</div>
<script>
if('serviceWorker' in navigator) navigator.serviceWorker.register('/sw.js');
let deferredPrompt;
window.addEventListener('beforeinstallprompt', e => {
  e.preventDefault();
  deferredPrompt = e;
  document.getElementById('install-btn').style.display = 'inline-block';
});
document.getElementById('install-btn')?.addEventListener('click', async () => {
  if(deferredPrompt) { deferredPrompt.prompt(); await deferredPrompt.userChoice; deferredPrompt = null; }
  document.getElementById('install-btn').style.display = 'none';
});
</script>
</body>
</html>
"""

@app.get("/invoice_wish", response_class=HTMLResponse)
def ui():
    return r"""<!DOCTYPE html>
<html lang="zh-TW">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-title" content="希望">
<meta name="theme-color" content="#1a237e">
<link rel="manifest" href="/manifest.json">
<link rel="apple-touch-icon" href="/icon-512.svg">
<title>希望 - 報帳助理</title>
<script>if('serviceWorker' in navigator) navigator.serviceWorker.register('/sw.js');</script>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:system-ui,sans-serif;background:#f5f5f0;color:#1a1a1a;padding:20px;max-width:640px;margin:0 auto}
h1{font-size:18px;font-weight:600;margin-bottom:4px}
.subtitle{font-size:13px;color:#888;margin-bottom:20px}
.card{background:#fff;border-radius:12px;padding:20px;margin-bottom:14px;border:1px solid #e0ddd6}
.card h2{font-size:14px;font-weight:600;margin-bottom:12px;display:flex;align-items:center;gap:8px}
.step-badge{display:inline-flex;align-items:center;justify-content:center;width:22px;height:22px;border-radius:50%;background:#eef2ff;color:#4f46e5;font-size:11px;font-weight:700;flex-shrink:0}
.step-badge.done{background:#dcfce7;color:#166534}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:8px}
.full{grid-column:1/-1}
label{font-size:12px;color:#555;display:block;margin-bottom:3px}
input,textarea,select{width:100%;padding:7px 10px;border:1px solid #ddd;border-radius:8px;font-size:13px;font-family:inherit}
textarea{resize:vertical;min-height:50px}
.btn{padding:8px 16px;border:none;border-radius:8px;font-size:13px;cursor:pointer;font-weight:500;transition:opacity .15s}
.btn:disabled{opacity:.4;cursor:not-allowed}
.btn-primary{background:#4f46e5;color:#fff}
.btn-success{background:#16a34a;color:#fff}
.btn-danger{background:#dc2626;color:#fff}
.btn-ghost{background:#f0f0ee;color:#333}
.btn-row{display:flex;gap:8px;margin-top:12px;flex-wrap:wrap}
.msg{font-size:12px;padding:8px 10px;border-radius:8px;margin-top:10px}
.msg.ok{background:#dcfce7;color:#166534}
.msg.err{background:#fee2e2;color:#991b1b}
.msg.info{background:#eff6ff;color:#1e40af}
.locked{opacity:.45;pointer-events:none}
.invoice-field{margin-bottom:8px}
.tag{display:inline-block;background:#eef2ff;color:#4f46e5;border-radius:4px;padding:1px 6px;font-size:11px}
.warn{background:#fefce8;border:1px solid #fde68a;color:#92400e;border-radius:8px;padding:10px 12px;font-size:12px;margin-top:8px}
#drop-zone{border:2px dashed #ccc;border-radius:10px;padding:30px;text-align:center;cursor:pointer;font-size:13px;color:#888;transition:border-color .2s}
#drop-zone.drag{border-color:#4f46e5;background:#eef2ff}
#preview{max-width:100%;border-radius:8px;margin-top:10px;display:none}
.spinner{display:inline-block;width:14px;height:14px;border:2px solid #ccc;border-top-color:#4f46e5;border-radius:50%;animation:spin .6s linear infinite;vertical-align:middle;margin-right:6px}
@keyframes spin{to{transform:rotate(360deg)}}
.items-table{width:100%;border-collapse:collapse;font-size:12px;margin-top:6px}
.items-table th{background:#f5f5f0;padding:5px 8px;text-align:left;font-weight:500}
.items-table td{padding:5px 8px;border-top:1px solid #eee}
.top-nav{display:flex;justify-content:space-between;align-items:center;margin-bottom:14px}
.invoice-list{display:grid;gap:8px;margin:10px 0 14px}
.invoice-summary{display:flex;align-items:center;justify-content:space-between;gap:10px;padding:10px 12px;border:1px solid #e0ddd6;border-radius:9px;background:#fafaf8}
.invoice-summary-main{min-width:0;font-size:12px;color:#555}
.invoice-summary-main strong{display:block;color:#222;font-size:13px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.failure-actions{display:none;margin-top:10px;padding-top:10px;border-top:1px solid #fecaca}
</style>
</head>
<body>
<h1>NTU 報帳助理</h1>
<div class="top-nav">
  <p class="subtitle" style="margin-bottom:0">資料於確認完成後立即從裝置刪除</p>
  <button class="btn btn-ghost" onclick="goHome()">⌂ 回主頁並清除資料</button>
</div>

<!-- Step 1: Payee info -->
<div class="card" id="card-payee">
  <h2><span class="step-badge" id="badge-1">1</span>輸入受款人資訊</h2>
  <div class="grid">
    <div><label>身分證字號 *</label><input id="p-id" placeholder="F123456789" autocomplete="off"></div>
    <div><label>姓名 *</label><input id="p-name" placeholder="王小明" autocomplete="off"></div>
    <div class="full"><label>銀行名稱 / 代碼 *</label>
      <select id="p-bcode">
        <option value="008">華南 (008)</option>
        <option value="808">玉山 (808)</option>
        <option value="700">郵局 (700)</option>
      </select>
    </div>
    <div class="full"><label>存款帳號 *</label><input id="p-acct" placeholder="00012345678" autocomplete="off"></div>
  </div>
  <div class="btn-row">
    <button class="btn btn-primary" onclick="savePayee()">儲存並繼續</button>
    <button class="btn btn-ghost" onclick="goHome()">回主頁並清除資料</button>
  </div>
  <div id="msg-payee"></div>
</div>

<!-- Step 2: Upload invoice -->
<div class="card locked" id="card-upload">
  <h2><span class="step-badge" id="badge-2">2</span>上傳發票 / 收據</h2>
  <div id="drop-zone" onclick="document.getElementById('file-input').click()"
       ondragover="event.preventDefault();this.classList.add('drag')"
       ondragleave="this.classList.remove('drag')"
       ondrop="handleDrop(event)">
    點擊或拖曳圖片到此處<br><small>支援 JPG / PNG / WEBP</small>
  </div>
  <div class="btn-row" id="camera-btn-row" style="margin-top:10px; justify-content:center;">
    <button class="btn btn-ghost" onclick="startCamera()">📷 使用電腦相機拍照</button>
  </div>
  <div id="camera-container" style="display:none; margin-top:10px; text-align:center;">
    <select id="camera-select" onchange="switchCamera(this.value)" style="margin-bottom: 8px; width:100%; display:none; padding:5px; border-radius:4px;"></select>
    <video id="camera-video" autoplay playsinline style="max-width:100%; border-radius:8px;"></video>
    <div class="btn-row" style="justify-content:center; margin-top:8px;">
      <button class="btn btn-primary" onclick="takeSnapshot()">📸 拍照並上傳</button>
      <button class="btn btn-ghost" onclick="stopCamera()">取消</button>
    </div>
  </div>
  <input type="file" id="file-input" accept="image/*" style="display:none" onchange="handleFile(this.files[0])">
  <input type="file" id="camera-input" accept="image/*" capture="environment" style="display:none" onchange="handleFile(this.files[0])">
  <img id="preview">
  <div id="msg-upload"></div>
</div>

<!-- Step 3: Review OCR -->
<div class="card locked" id="card-review">
  <h2><span class="step-badge" id="badge-3">3</span>確認 / 修改 OCR 結果</h2>
  <div class="warn" id="ocr-warn" style="display:none">⚠️ 請仔細核對以下資料，如有錯誤請直接修改後再確認。</div>
  <div class="grid" style="margin-top:10px">
    <div><label>發票號碼</label><input id="r-invnum" autocomplete="off"></div>
    <div><label>日期</label><input id="r-date" placeholder="YYYY-MM-DD" autocomplete="off"></div>
    <div class="full"><label>金額（元）</label><input id="r-amount" type="number" autocomplete="off"></div>
    <div class="full"><label>用途及摘要（填入報帳系統）</label><input id="r-purpose" autocomplete="off"></div>
  </div>
  <div style="margin-top:10px">
    <label style="font-size:12px;color:#555;margin-bottom:4px;display:block">品項明細（供參考，不填入系統）</label>
    <table class="items-table" id="items-table">
      <thead><tr><th>品名</th><th>數量</th><th>單價</th><th style="width:40px"></th></tr></thead>
      <tbody id="items-body"></tbody>
    </table>
    <button class="btn btn-ghost" onclick="addItemRow()" style="margin-top:6px;font-size:12px;">＋ 新增品項</button>
  </div>
  <div class="btn-row">
    <button class="btn btn-success" id="btn-confirm-invoice" onclick="confirmInvoice()">確認此筆發票</button>
    <button class="btn btn-ghost" onclick="resetOcr()">重新上傳此筆</button>
    <button class="btn btn-ghost" onclick="backToPayee()">回上一步修改個人資料</button>
  </div>
  <div id="msg-review"></div>
  <div id="invoice-actions" style="display:none; margin-top:15px; padding-top:15px; border-top:1px solid #eee;">
    <p style="font-size:13px; margin-bottom:10px;">目前已確認 <strong id="inv-count">0</strong> 筆發票。</p>
    <div class="btn-row">
      <button class="btn btn-ghost" onclick="addAnotherInvoice()">新增另一筆發票</button>
      <button class="btn btn-primary" onclick="finishInvoices()">全部確認並自動執行 Agent</button>
    </div>
  </div>
</div>

<!-- Step 4: Login + Agent running -->
<div class="card locked" id="card-agent">
  <h2><span class="step-badge" id="badge-4">4</span>登入並執行 Agent</h2>
  <div>
    <label>本輪已確認發票</label>
    <div class="invoice-list" id="agent-invoice-list"></div>
  </div>
  <p style="font-size:13px;color:#555">全部發票確認後，系統會自動登入並執行報帳任務。</p>
  <div class="btn-row">
    <button class="btn btn-primary" id="btn-run-agent" onclick="runAgent()" style="display:none">執行 Agent</button>
    <button class="btn btn-ghost" onclick="backToInvoices()">回上一步修改發票</button>
    <button class="btn btn-ghost" onclick="backToPayee()">修改個人資料</button>
    <button class="btn btn-ghost" onclick="goHome()">回主頁並清除資料</button>
  </div>
  <div id="msg-agent" style="margin-top:10px"></div>
  <div class="failure-actions" id="agent-failure-actions">
    <p style="font-size:12px;color:#991b1b">自動重試仍未完成。可保留目前資料手動重試；若再次失敗，仍可繼續按此按鈕重試。</p>
    <div class="btn-row">
      <button class="btn btn-primary" id="btn-manual-retry" onclick="runAgent(true)">手動重試</button>
      <button class="btn btn-danger" onclick="clearAndGoHome()">清除資料並回主頁</button>
    </div>
  </div>
</div>

<!-- Step 5: Done + wipe -->
<div class="card locked" id="card-done">
  <h2><span class="step-badge" id="badge-5">5</span>完成確認 &amp; 資料清除</h2>
  <p style="font-size:13px;color:#555">報帳條碼：<strong id="report-number">—</strong></p>
  <div id="print-link-row" style="margin-top:10px;display:none">
    <a id="print-link" href="/api/print-page" target="_blank" class="btn btn-primary" style="text-decoration:none">🖨️ 開啟黏存單列印</a>
  </div>
  <p style="font-size:13px;color:#555;margin-top:10px">請確認列印黏存單已完成，然後點下方按鈕清除本機所有個人資料。</p>
  <div class="btn-row">
    <button class="btn btn-danger" onclick="wipeData()">確認完成，立即清除個人資料</button>
  </div>
  <div id="msg-done"></div>
</div>

<script>
let currentInvoices = [];
let editingInvoiceIndex = null;
const unlock = id => document.getElementById(id).classList.remove('locked');
const setMsg = (id, text, type) => {
  const el = document.getElementById(id);
  el.className = 'msg ' + type;
  el.textContent = text;
};
const markDone = n => {
  const b = document.getElementById('badge-' + n);
  b.textContent = '✓';
  b.classList.add('done');
};

async function savePayee() {
  const body = {
    payee_id: document.getElementById('p-id').value.trim(),
    payee_name: document.getElementById('p-name').value.trim(),
    bank_code: document.getElementById('p-bcode').value.trim(),

    account_number: document.getElementById('p-acct').value.trim(),

  };
  if (!body.payee_id || !body.payee_name || !body.bank_code || !body.account_number) {
    setMsg('msg-payee', '請填寫所有必填欄位', 'err'); return;
  }
  const res = await fetch('/api/payee', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)});
  if (res.ok) {
    setMsg('msg-payee', '已儲存（資料僅存於本機，提交後自動刪除）', 'ok');
    markDone(1);
    unlock('card-upload');
    if (currentInvoices.length) document.getElementById('btn-run-agent').disabled = false;
  } else {
    setMsg('msg-payee', '儲存失敗', 'err');
  }
}

function goHome() {
  return clearAndGoHome();
}

function backToPayee() {
  unlock('card-payee');
  document.getElementById('btn-run-agent').disabled = true;
  document.getElementById('card-payee').scrollIntoView({behavior:'smooth'});
  setMsg('msg-payee', '可直接修改資料，再按「儲存並繼續」。', 'info');
}

function backToInvoices() {
  unlock('card-upload');
  unlock('card-review');
  document.getElementById('card-review').scrollIntoView({behavior:'smooth'});
  setMsg('msg-review', '請從 Agent 上方的發票清單選擇「編輯」，或新增另一筆發票。', 'info');
}

function handleDrop(e) {
  e.preventDefault();
  document.getElementById('drop-zone').classList.remove('drag');
  const file = e.dataTransfer.files[0];
  if (file) handleFile(file);
}

async function handleFile(file) {
  if (!file) return;
  // Show preview
  const reader = new FileReader();
  reader.onload = e => {
    const img = document.getElementById('preview');
    img.src = e.target.result;
    img.style.display = 'block';
  };
  reader.readAsDataURL(file);

  setMsg('msg-upload', '<span class="spinner"></span>OCR 辨識中，請稍候...', 'info');
  document.getElementById('msg-upload').innerHTML = '<span class="spinner"></span>OCR 辨識中，請稍候...';

  const formData = new FormData();
  formData.append('file', file);
  const res = await fetch('/api/upload', {method:'POST', body:formData});
  if (!res.ok) {
    const err = await res.json();
    setMsg('msg-upload', '上傳失敗：' + (err.detail || '未知錯誤'), 'err');
    return;
  }
  const data = await res.json();
  populateReview(data);
  document.getElementById('invoice-actions').style.display = 'none';
  markDone(2);
  unlock('card-review');
  setMsg('msg-upload', 'OCR 完成，請至步驟 3 確認結果', 'ok');
}

let stream = null;
async function startCamera(deviceId = null) {
  // If getUserMedia not available (non-HTTPS), fall back to native camera
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    document.getElementById('camera-input').click();
    return;
  }

  document.getElementById('drop-zone').style.display = 'none';
  document.getElementById('camera-btn-row').style.display = 'none';
  const container = document.getElementById('camera-container');
  container.style.display = 'block';

  if (stream) {
    stream.getTracks().forEach(t => t.stop());
    stream = null;
  }

  const constraints = {
    video: deviceId ? { deviceId: { exact: deviceId } } : { facingMode: 'environment', width: { ideal: 1920 }, height: { ideal: 1080 } }
  };

  try {
    stream = await navigator.mediaDevices.getUserMedia(constraints);
    const video = document.getElementById('camera-video');
    video.srcObject = stream;
    await video.play();

    // Populate camera dropdown if it's the first time
    if (!deviceId) {
      const devices = await navigator.mediaDevices.enumerateDevices();
      const videoDevices = devices.filter(d => d.kind === 'videoinput');
      const select = document.getElementById('camera-select');
      if (videoDevices.length > 1) {
        select.innerHTML = '';
        videoDevices.forEach(d => {
          const opt = document.createElement('option');
          opt.value = d.deviceId;
          opt.text = d.label || `Camera ${select.length + 1}`;
          select.appendChild(opt);
        });
        select.style.display = 'block';
      }
    }
  } catch (err) {
    console.error('Camera error:', err);
    if (err.name === 'NotAllowedError') {
      alert("相機權限被拒絕。請在瀏覽器設定中允許相機存取。");
    } else if (err.name === 'NotFoundError') {
      alert("找不到相機裝置。請確認相機已連接。");
    } else if (err.name === 'NotReadableError') {
      alert("相機被其他程式佔用，請關閉其他使用相機的應用程式後重試。");
    } else {
      alert("無法存取相機：" + err.message + "\n\n如非 localhost 連線，請改用 http://localhost:8001/invoice_wish");
    }
    stopCamera();
  }
}
function switchCamera(deviceId) {
  startCamera(deviceId);
}
function stopCamera() {
  if(stream) { stream.getTracks().forEach(t => t.stop()); stream = null; }
  document.getElementById('camera-container').style.display = 'none';
  document.getElementById('drop-zone').style.display = 'block';
  document.getElementById('camera-btn-row').style.display = 'flex';
}
function takeSnapshot() {
  const video = document.getElementById('camera-video');
  const canvas = document.createElement('canvas');
  canvas.width = video.videoWidth;
  canvas.height = video.videoHeight;
  canvas.getContext('2d').drawImage(video, 0, 0);
  canvas.toBlob(blob => {
    stopCamera();
    const file = new File([blob], "snapshot.jpg", { type: "image/jpeg" });
    handleFile(file);
  }, 'image/jpeg');
}

function populateReview(d) {
  let invNum = d.invoice_number || '';
  const dateVal = d.date || '';
  if (invNum.replace(/\s/g, '').toUpperCase() === 'NOTFOUND' || invNum === '') {
    // Generate ROC date: (year-1911)MMDD
    const parts = dateVal.split('-');
    if (parts.length === 3) {
      const rocYear = parseInt(parts[0]) - 1911;
      invNum = rocYear + parts[1] + parts[2];
    } else {
      invNum = dateVal.replace(/[^0-9]/g, '');
    }
  }
  document.getElementById('r-invnum').value = invNum;
  document.getElementById('r-date').value = dateVal;
  // Cap amount at 2000
  let amount = parseInt(d.total_amount) || 0;
  if (amount > 2000) amount = 2000;
  document.getElementById('r-amount').value = amount || d.total_amount || '';
  document.getElementById('r-purpose').value = d.expense_purpose || '';

  const tbody = document.getElementById('items-body');
  tbody.innerHTML = '';
  (d.items || []).forEach(it => {
    addItemRow(it.name || '', it.qty || 1, it.price || '');
  });

  const low = parseInt(d.confidence_score || 100);
  document.getElementById('ocr-warn').style.display = low < 80 ? 'block' : 'none';
}

function makeInvoiceBody() {
  const items = [];
  document.querySelectorAll('#items-body tr').forEach(tr => {
    const name = tr.querySelector('.item-name');
    const qty = tr.querySelector('.item-qty');
    const price = tr.querySelector('.item-price');
    if (name) items.push({name: name.value, qty: qty ? qty.value : '1', price: price ? price.value : ''});
  });
  return {
    invoice_number: document.getElementById('r-invnum').value.trim(),
    date: document.getElementById('r-date').value.trim(),
    total_amount: document.getElementById('r-amount').value.trim(),
    items,
    expense_purpose: document.getElementById('r-purpose').value.trim(),
  };
}

async function loadInvoices() {
  const res = await fetch('/api/invoices');
  if (!res.ok) return;
  const data = await res.json();
  currentInvoices = data.invoices || [];
  renderInvoiceList();
}

function renderInvoiceList() {
  const list = document.getElementById('agent-invoice-list');
  list.innerHTML = '';
  currentInvoices.forEach((invoice, index) => {
    const row = document.createElement('div');
    row.className = 'invoice-summary';
    const main = document.createElement('div');
    main.className = 'invoice-summary-main';
    const title = document.createElement('strong');
    title.textContent = `第 ${index + 1} 筆｜${invoice.invoice_number || '無號碼'}`;
    const detail = document.createElement('span');
    detail.textContent = `${invoice.date || '無日期'}｜NT$ ${invoice.total_amount || '0'}｜${invoice.expense_purpose || '無摘要'}`;
    main.append(title, detail);
    const edit = document.createElement('button');
    edit.className = 'btn btn-ghost';
    edit.textContent = '編輯';
    edit.onclick = () => editInvoice(index);
    row.append(main, edit);
    list.appendChild(row);
  });
  if (!currentInvoices.length) {
    const empty = document.createElement('div');
    empty.className = 'msg info';
    empty.textContent = '尚未確認發票。';
    list.appendChild(empty);
  }
  document.getElementById('inv-count').textContent = currentInvoices.length;
}

function editInvoice(index) {
  const invoice = currentInvoices[index];
  if (!invoice) return;
  editingInvoiceIndex = index;
  document.getElementById('btn-run-agent').disabled = true;
  unlock('card-review');
  document.querySelectorAll('#card-review input').forEach(el => el.disabled = false);
  populateReview(invoice);
  document.getElementById('btn-confirm-invoice').textContent = '儲存修改';
  document.getElementById('invoice-actions').style.display = 'none';
  setMsg('msg-review', `正在編輯第 ${index + 1} 筆發票`, 'info');
  document.getElementById('card-review').scrollIntoView({behavior:'smooth'});
}

function addItemRow(name, qty, price) {
  const tbody = document.getElementById('items-body');
  const tr = document.createElement('tr');
  const fields = [
    {type:'text', value:name || '', className:'item-name', width:'100%'},
    {type:'number', value:qty || 1, className:'item-qty', width:'60px'},
    {type:'number', value:price || '', className:'item-price', width:'80px'},
  ];
  fields.forEach(field => {
    const td = document.createElement('td');
    const input = document.createElement('input');
    input.type = field.type;
    input.value = field.value;
    input.className = field.className;
    input.style.width = field.width;
    td.appendChild(input);
    tr.appendChild(td);
  });
  const actionCell = document.createElement('td');
  const remove = document.createElement('button');
  remove.className = 'btn btn-ghost';
  remove.textContent = '✕';
  remove.style.cssText = 'padding:2px 6px;font-size:11px;color:#e74c3c';
  remove.onclick = () => tr.remove();
  actionCell.appendChild(remove);
  tr.appendChild(actionCell);
  tbody.appendChild(tr);
}

async function confirmInvoice() {
  const body = makeInvoiceBody();
  if (!body.invoice_number || !body.total_amount) {
    setMsg('msg-review', '發票號碼和金額為必填', 'err'); return;
  }
  const isEditing = editingInvoiceIndex !== null;
  const url = isEditing ? `/api/invoices/${editingInvoiceIndex}` : '/api/confirm-invoice';
  const res = await fetch(url, {method:isEditing ? 'PUT' : 'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)});
  if (res.ok) {
    const data = await res.json();
    setMsg('msg-review', isEditing ? '發票修改已儲存。' : '已確認此筆發票。', 'ok');
    editingInvoiceIndex = null;
    document.getElementById('btn-confirm-invoice').textContent = '確認此筆發票';
    document.getElementById('invoice-actions').style.display = 'block';
    document.getElementById('inv-count').textContent = data.count;
    document.querySelectorAll('#card-review input').forEach(el => {
      el.value = '';
      el.disabled = true;
    });
    document.getElementById('items-body').innerHTML = '';
    await loadInvoices();
    if (isEditing) document.getElementById('btn-run-agent').disabled = false;
  } else {
    const err = await res.json();
    setMsg('msg-review', '儲存失敗：' + (err.detail || '未知錯誤'), 'err');
  }
}

function addAnotherInvoice() {
  editingInvoiceIndex = null;
  document.getElementById('btn-confirm-invoice').textContent = '確認此筆發票';
  document.getElementById('btn-run-agent').disabled = true;
  document.querySelectorAll('#card-review input').forEach(el => el.disabled = false);
  document.getElementById('invoice-actions').style.display = 'none';
  setMsg('msg-review', '', '');
  resetOcr();
  // Scroll to Step 2 upload card instead of opening file dialog
  document.getElementById('card-upload').scrollIntoView({ behavior: 'smooth' });
}

async function finishInvoices() {
  markDone(3);
  unlock('card-agent');
  document.getElementById('btn-run-agent').disabled = false;
  setMsg('msg-review', '發票皆已確認，正在自動啟動 Agent。', 'ok');
  await loadInvoices();
  document.getElementById('card-agent').scrollIntoView({behavior:'smooth'});
  await runAgent(false);
}

function resetOcr() {
  document.getElementById('preview').style.display = 'none';
  document.getElementById('file-input').value = '';
  setMsg('msg-upload', '', '');
}

async function runAgent(manual = false) {
  document.getElementById('btn-run-agent').disabled = true;
  document.getElementById('btn-manual-retry').disabled = true;
  document.getElementById('agent-failure-actions').style.display = 'none';
  document.getElementById('msg-agent').innerHTML = manual
    ? '<span class="spinner"></span>手動重試中；若失敗仍可再次重試...'
    : '<span class="spinner"></span>Agent 執行中；失敗時最多自動嘗試 3 次...';
  const res = await fetch('/api/run-agent' + (manual ? '?manual=true' : ''), {method:'POST'});
  if (res.ok) {
    const data = await res.json();
    document.getElementById('report-number').textContent = data.report_number || '—';
    document.getElementById('msg-agent').className = 'msg ok';
    document.getElementById('msg-agent').textContent = `Agent 完成！（共嘗試 ${data.attempts || 1} 次）`;
    document.getElementById('print-link-row').style.display = 'block';
    markDone(4);
    unlock('card-done');
  } else {
    const err = await res.json();
    document.getElementById('msg-agent').className = 'msg err';
    document.getElementById('msg-agent').textContent = 'Agent 錯誤：' + (err.detail || '未知');
    document.getElementById('btn-run-agent').disabled = false;
    document.getElementById('btn-manual-retry').disabled = false;
    document.getElementById('agent-failure-actions').style.display = 'block';
  }
}

async function clearAndGoHome() {
  if (!confirm('回主頁將清除本輪個人資料與發票，確定繼續？')) return;
  try {
    const res = await fetch('/api/wipe', {method:'POST'});
    if (!res.ok) {
      alert('資料清除失敗，為避免資料殘留，目前不會離開此頁。');
      return;
    }
    window.location.href = '/';
  } catch (err) {
    alert('無法連線清除資料，為避免資料殘留，目前不會離開此頁。');
  }
}

async function wipeData() {
  if (!confirm('確定已完成列印黏存單？確認後個人資料將立即從本機刪除。')) return;
  const res = await fetch('/api/wipe', {method:'POST'});
  if (res.ok) {
    setMsg('msg-done', '✓ 個人資料已清除。畫面即將重置...', 'ok');
    markDone(5);
    // Clear all form fields in browser too
    document.querySelectorAll('input, textarea').forEach(el => el.value = '');
    document.getElementById('preview').style.display = 'none';

    // 設定延遲 2 秒後自動重新整理頁面
    setTimeout(() => {
        window.location.href = '/';
    }, 2000);
  }
}

async function restoreSessionState() {
  try {
    const res = await fetch('/api/session');
    if (!res.ok) return;
    const session = await res.json();
    if (session.payee) {
      markDone(1);
      unlock('card-upload');
      setMsg(
        'msg-payee',
        `已保留受款人：${session.payee.payee_name || ''}（${session.payee.payee_id_hint || ''}）。如需修改，請重新填寫完整資料。`,
        'info'
      );
    }
    currentInvoices = session.invoices || [];
    renderInvoiceList();
    if (currentInvoices.length) {
      markDone(2);
      markDone(3);
      unlock('card-review');
      unlock('card-agent');
      document.getElementById('invoice-actions').style.display = 'block';
    }
    if (session.step === 'agent_done') {
      markDone(4);
      unlock('card-done');
      document.getElementById('report-number').textContent = session.report_number || '—';
      document.getElementById('print-link-row').style.display = 'block';
    }
  } catch (err) {
    console.error('Unable to restore session state:', err);
  }
}

restoreSessionState();
</script>
</body>
</html>"""

def _ensure_ssl_certs():
    """Generate self-signed SSL cert for HTTPS (enables camera on any network)."""
    cert_dir = os.path.dirname(os.path.abspath(__file__))
    cert_file = os.path.join(cert_dir, "cert.pem")
    key_file = os.path.join(cert_dir, "key.pem")
    if not os.path.exists(cert_file):
        print("🔐 Generating self-signed SSL certificate...")
        subprocess.run([
            "openssl", "req", "-x509", "-newkey", "rsa:2048",
            "-keyout", key_file, "-out", cert_file,
            "-days", "365", "-nodes",
            "-subj", "/CN=invoice-agent"
        ], check=True, capture_output=True)
    return cert_file, key_file

if __name__ == "__main__":
    try:
        cert, key = _ensure_ssl_certs()
        print("🚀 Starting HTTPS server on https://0.0.0.0:8001")
        uvicorn.run(app, host="0.0.0.0", port=8001, ssl_keyfile=key, ssl_certfile=cert)
    except Exception:
        print("⚠️ openssl not found, falling back to HTTP (camera may not work on non-localhost)")
        uvicorn.run(app, host="0.0.0.0", port=8001)
