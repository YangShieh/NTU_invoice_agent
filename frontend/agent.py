"""
NTU Expense Report Agent (Macro-Tool Architecture)
Called by session_ui.py via run_submission(), or directly:
  python agent.py --session session.json
"""

import argparse, json, os, re, sys, time
from openai import OpenAI
from playwright.sync_api import sync_playwright, Page

def _safe_accept(dialog):
    """Accept dialogs safely — prevents TargetClosedError if page navigates away."""
    try:
        dialog.accept()
    except Exception:
        pass

# ── Config ────────────────────────────────────────────────────────────────────

def load_config() -> dict:
    path = os.path.join(os.path.dirname(__file__), "config.json")
    if not os.path.exists(path):
        sys.exit("❌ config.json not found.")
    with open(path, encoding="utf-8") as f:
        return json.load(f)

# ── LLM tools spec ───────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are an NTU expense report automation agent.
Do not attempt to visually reason about the UI layout or guess buttons. 
Only use the provided workflow tools in this exact sequence to complete the task:

1. Call `get_page_state()` to verify you are logged in.
2. Call `open_plan_expense_page()` to navigate to the correct form.
3. Call `submit_project_code` with the provided PROJECT CODE.
4. Call `select_project_row` to select the project and move to the invoice step.
5. Call `add_invoice` for EACH invoice provided in the list.
6. Call `add_payee` with the exact payee details and the total amount of all invoices.
7. Call `print_receipt` to finalize the report.
8. Call `report_done` with the final report number.

If any tool returns an ERROR string, immediately call `report_error` with the exact error text."""

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_page_state",
            "description": "Get current page URL and visible text. Use this to verify state before starting.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "open_plan_expense_page",
            "description": "Navigates the top menu to 報帳管理 -> 計畫經費報帳.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "submit_project_code",
            "description": "Fills in the project code and submits the search form.",
            "parameters": {
                "type": "object",
                "properties": {
                    "project_code": {"type": "string", "description": "e.g., 114L3073"},
                },
                "required": ["project_code"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "select_project_row",
            "description": "Selects the specific project from the data table and clicks 'Next'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "project_code": {"type": "string"},
                },
                "required": ["project_code"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_invoice",
            "description": "Fills out the invoice row (number, purpose, amount). If it is the last invoice, set is_last_invoice to true to click 'Next'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "invoice_number": {"type": "string"},
                    "purpose": {"type": "string"},
                    "amount": {"type": "string"},
                    "is_last_invoice": {"type": "boolean"},
                },
                "required": ["invoice_number", "purpose", "amount", "is_last_invoice"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_payee",
            "description": "Sets payment to Transfer, fills payee ID and notes, looks up account, and saves.",
            "parameters": {
                "type": "object",
                "properties": {
                    "payee_id": {"type": "string"},
                    "note": {"type": "string"},
                    "amount": {"type": "string"},
                },
                "required": ["payee_id", "note", "amount"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "print_receipt",
            "description": "Clicks the final '列印黏存單' button to generate the report.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "report_done",
            "description": "Call when task is complete.",
            "parameters": {
                "type": "object",
                "properties": {"report_number": {"type": "string"}},
                "required": ["report_number"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "report_error",
            "description": "Call when an unrecoverable error is encountered in any tool.",
            "parameters": {
                "type": "object",
                "properties": {"reason": {"type": "string"}},
                "required": ["reason"],
            },
        },
    },
]

# ── Playwright Macro-Tools ───────────────────────────────────────────────────

def get_page_state(page: Page) -> str:
    """The 'Vacuum Cleaner' frame-aware state extraction."""
    try:
        page.wait_for_load_state("domcontentloaded", timeout=3000)
    except Exception:
        pass
    time.sleep(0.5)

    url = page.url
    all_text = []

    for frame in page.frames:
        try:
            text = frame.evaluate("""() => {
                const sel = 'label, button, a, th, td, input, textarea, select, h1, h2, h3, p, span, div, font, b, li';
                const els = document.querySelectorAll(sel);
                const lines = [];

                for (const el of els) {
                    let t = '';
                    if (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA') {
                        t = el.value || el.placeholder || '';
                    } else if (el.tagName === 'SELECT') {
                        t = el.options[el.selectedIndex]?.text || '';
                    } else {
                        t = el.innerText || el.textContent || '';
                    }
                    t = t.trim().replace(/\\s+/g, ' ');
                    if (t && t !== ' ' && !lines.includes(t)) {
                        lines.push(t);
                    }
                }
                return lines.join(' | ');
            }""")
            if text:
                all_text.append(text)
        except Exception:
            continue

    combined_text = " | ".join(all_text)
    seen = set()
    final_items = []
    
    for item in combined_text.split(" | "):
        item = item.strip()
        if item and item not in seen:
            seen.add(item)
            final_items.append(item)

    final_text = " | ".join(final_items)[:8000]
    return f"URL: {url}\nPAGE: {final_text}"


def open_plan_expense_page(page: Page) -> str:
    """Navigates the top-level menu safely."""
    try:
        page.goto("https://ntuacc.cc.ntu.edu.tw/acc/apply/list.asp")
        page.wait_for_load_state("networkidle")
        return "Navigated to 報帳管理 -> 計畫經費報帳 directly via URL."
    except Exception as e:
        return f"ERROR navigating to page: {e}"


def submit_project_code(page: Page, project_code: str) -> str:
    try:
        # 1. Search all frames for the bugetno input (NTU uses frame layouts)
        target_frame = None
        input_box = None
        for frame in page.frames:
            box = frame.locator("input[type='text'][name='bugetno']")
            if box.count() > 0:
                target_frame = frame
                input_box = box
                break
            
        if target_frame and input_box:
            # Fail fast if obscured, instead of hanging for 30s
            input_box.first.click(timeout=5000) 
            time.sleep(0.3) # Allow legacy JS to clear the fake placeholder
            input_box.first.clear()
            input_box.first.fill(project_code)

            # 2. Target the exact submit button in the same frame
            submit_btn = target_frame.locator("input[type='submit'][name='act'][value='報帳']")

            if submit_btn.count() > 0:
                # 3. State-Based Waiting (Navigation Safe)
                try:
                    with page.expect_navigation(timeout=10000):
                        submit_btn.first.click(timeout=5000)
                    
                    page.wait_for_load_state("networkidle")
                    return f"OK - Form submitted. Navigated to next step for {project_code}."
                except Exception as e:
                    return f"ERROR: Form submitted, but page navigation timed out or failed. {str(e)}"

        return "ERROR: Could not find the visible project code input area (bugetno) on this page."
    except Exception as e:
        return f"ERROR in submit_project_code: {str(e)}"

def select_project_row(page: Page, project_code: str) -> str:
    """Clicks the project row and hits the next step button."""
    try:
        for frame in page.frames:
            row = frame.locator(f"tr:has-text('{project_code}')")
            if row.count() > 0:
                row.first.click()
                time.sleep(0.5)
                
                # Find and click '下一步'
                next_btn = frame.locator("input[value='下一步'], button:has-text('下一步')")
                if next_btn.count() > 0:
                    next_btn.first.click()
                    page.wait_for_load_state("networkidle")
                    return "OK - Project selected, moved to Next Step."
                
        return f"ERROR: Could not find row for {project_code} or '下一步' button."
    except Exception as e:
        return f"ERROR in select_project_row: {e}"


def truncate_purpose(purpose: str, max_len: int = 50) -> str:
    result = ""
    current_len = 0
    for char in purpose:
        char_len = 2 if ord(char) > 127 else 1
        if current_len + char_len > max_len:
            break
        result += char
        current_len += char_len
    return result

def add_invoice(page: Page, invoice_number: str, purpose: str, amount: str, is_last_invoice: bool) -> str:
    """Handles the invoice data entry. Clicks Next Step only if is_last_invoice is true."""
    purpose = truncate_purpose(purpose, 50)
    try:
        # 1. 確保欄位已出現
        add_btn = page.locator("input[value='新增'], button:has-text('新增')")
        if add_btn.count() > 0:
            try:
                add_btn.first.click(timeout=5000)
                page.wait_for_load_state("networkidle", timeout=5000)
                time.sleep(1.0)
            except: pass
                
        # 2. 重試迴圈
        for attempt in range(2): # 最多重試 2 次
            try:
                # 填寫欄位
                inv_box = page.locator("input[name='vno']")
                inv_box.first.fill(invoice_number)
                inv_box.first.press("Tab")
                page.locator("input[name='subj']").first.fill(purpose)
                page.locator("input[name='amt']").first.fill(amount)
                time.sleep(0.5)
                
                # 點擊儲存
                save_btn = page.locator("input[type='submit'][name='act'][value='儲存']")
                if save_btn.count() > 0:
                    try:
                        page.once("dialog", _safe_accept)
                        save_btn.first.click(timeout=5000)
                    except:
                        pass
                    time.sleep(2.0) # 等待後端處理
                
                # Verify if it saved successfully
                saved_row = page.locator(f"tr:has-text('{invoice_number}')")
                if saved_row.count() == 0:
                    print(f"⚠️ Attempt {attempt+1}: Invoice {invoice_number} not found in list, retrying...")
                    time.sleep(1.0)
                    continue
                
                # 檢查下一步是否可用
                if is_last_invoice:
                    next_btn = page.locator("input[type='submit'][name='act'][value='下一步']:not([disabled])")
                    if next_btn.count() > 0:
                        with page.expect_navigation(timeout=10000):
                            next_btn.first.click(timeout=5000)
                        return "OK - Invoice saved and moved to Next Step."
                    else:
                        print(f"⚠️ Attempt {attempt+1}: Next button still disabled, retrying...")
                        time.sleep(2.0)
                else:
                    return "OK - Invoice saved. Ready for another invoice."
            except Exception as e:
                print(f"⚠️ Attempt {attempt+1} failed: {e}")
                time.sleep(2.0)
        
        return "ERROR: Failed to save or proceed to next step after retries."
    except Exception as e:
        return f"ERROR in add_invoice: {str(e)}"


def add_payee(page: Page, payee_id: str, note: str, amount: str, payee: dict = None) -> str:
    """Handles Payee data entry with exact HTML names and forced fill for hidden elements."""
    try:
        # --- 1. 點擊「新增」按鈕 ---
        add_btn = page.locator("input[value='新增'], button:has-text('新增')")
        if add_btn.count() > 0:
            try:
                with page.expect_navigation(timeout=5000):
                    add_btn.first.click(timeout=5000)
            except Exception:
                pass
            page.wait_for_load_state("networkidle", timeout=5000)
            time.sleep(1.0)

        # --- 2. 選擇「匯代墊人」付款方式 ---
        try:
            for frame in page.frames:
                paymethod = frame.locator("select[name='paymethod'], select#paymethod")
                if paymethod.count() > 0:
                    paymethod.first.select_option(value="匯代墊人")
                    time.sleep(0.5)
                    break
        except:
            pass

        # --- 3. 精準定位 Input 欄位 (根據 HTML 原始碼) ---
        try:
            page.wait_for_selector("input[name='mid']", timeout=5000)
        except:
            pass

        payee_box = page.locator("input[name='mid']")
        note_box = page.locator("input[name='memo']")

        if payee_box.count() > 0:
            # 填寫受款人 ID
            payee_box.first.clear()
            payee_box.first.fill(payee_id)
            
            # 點擊 "..." 按鈕觸發 CheckBpeno() 檢查機制
            check_btn = page.locator("input[value='...'][onclick*='CheckBpeno']")
            if check_btn.count() > 0:
                check_btn.first.click()
                time.sleep(1.5) # 給予系統時間進行帳號檢核
            
            # 強制寫入指定的備註文字 (使用 force=True 繞過 display:none 限制)
            exact_note = "弱勢生教材費補助，市場慣例學生先墊付，不具典藏價值"
            if note_box.count() > 0:
                note_box.first.clear(force=True)
                note_box.first.fill(exact_note, force=True)

            remark_box = page.locator("textarea[name='remark']")
            if remark_box.count() > 0:
                remark_box.first.clear(force=True)
                remark_box.first.fill(exact_note, force=True)
            
            try:
                amt_val = int(float(amount))
            except Exception:
                amt_val = 0
            
            if amt_val > 2000:
                amt_val = 2000
                
            amt_box = page.locator("input[name='amt']")
            if amt_box.count() > 0:
                amt_box.first.clear(force=True)
                amt_box.first.fill(str(amt_val), force=True)

            # --- 設定 JS Alert 監聽器 (自動點擊確定) ---
            dialog_triggered = {"status": False, "handled": False}
            def handle_dialog(dialog):
                dialog_triggered["status"] = True
                if not dialog_triggered["handled"]:
                    dialog_triggered["handled"] = True
                    try:
                        dialog.accept()
                    except:
                        pass
            page.on("dialog", handle_dialog)

            # --- 4. 點擊「儲存」 ---
            save_btn = page.locator("input[type='submit'][value='儲存'], input[type='button'][value='儲存'], button:has-text('儲存')")
            if save_btn.count() > 0:
                save_btn.first.click(timeout=5000)
                try:
                    page.wait_for_load_state("networkidle", timeout=5000)
                except Exception:
                    pass
                time.sleep(1.5)
                
            # --- 5. 處理「新增受款人」的跳出視窗 (若受款人不存在) ---
            if dialog_triggered["status"] and payee:
                try:
                    print("⚠️ Payee not found, opening new tab to add payee...")
                    new_page = page.context.new_page()
                    new_page.goto("https://ntuacc.cc.ntu.edu.tw/acc/main.asp")
                    new_page.wait_for_load_state("networkidle")
                    time.sleep(1.0)
                    
                    # 嘗試 hover 報帳管理以觸發下拉選單 (如果是 CSS Menu)
                    for frame in new_page.frames:
                        try:
                            menu_item = frame.locator("text='報帳管理'")
                            if menu_item.count() > 0:
                                menu_item.first.hover(timeout=2000)
                                time.sleep(0.5)
                        except:
                            pass
                            
                    # 尋找並點擊/選擇 受款人管理
                    found_menu = False
                    for frame in new_page.frames:
                        payee_mgr_btn = frame.locator("a:has-text('受款人管理'), button:has-text('受款人管理')")
                        if payee_mgr_btn.count() > 0:
                            payee_mgr_btn.first.click(force=True, timeout=5000)
                            found_menu = True
                            break
                            
                        # 若為選單 (select) - 例如報帳管理是一個 select，受款人管理是裡面的 option
                        payee_select = frame.locator("select:has(option:has-text('受款人管理'))")
                        if payee_select.count() > 0:
                            payee_select.first.select_option(label="受款人管理")
                            try:
                                frame.evaluate("if(typeof change === 'function') change();")
                            except: pass
                            found_menu = True
                            break
                            
                    if found_menu:
                        new_page.wait_for_load_state("networkidle")
                        time.sleep(1.0)
                        
                        # 點擊 新增 (+)
                        found_add = False
                        for frame in new_page.frames:
                            # 根據使用者提供的 HTML: <img src="/acc/image/insert.gif" alt="新增" ...>
                            add_plus_btn = frame.locator("img[src*='insert.gif'], img[alt='新增'], input[value*='+'], input[value*='＋'], button:has-text('+'), button:has-text('＋'), a:has-text('+'), img[src*='add'], img[src*='plus']")
                            if add_plus_btn.count() > 0:
                                add_plus_btn.first.click(force=True, timeout=5000)
                                found_add = True
                                break
                            else:
                                fallback = frame.locator("text='新增'")
                                if fallback.count() > 0:
                                    fallback.first.click(force=True, timeout=5000)
                                    found_add = True
                                    break
                                    
                        new_page.wait_for_load_state("networkidle")
                        time.sleep(1.0)
                        
                        # 填寫受款人資料 (根據 PDF 畫面定位)
                        success_fill = False
                        for frame in new_page.frames:
                            id_input = frame.locator("input[name='ID']")
                            if id_input.count() > 0:
                                id_input.first.fill(payee.get('payee_id', ''))
                                frame.locator("input[name='MNAME']").first.fill(payee.get('payee_name', ''))
                                
                                # 銀行代碼是 readonly，強制用 JS 填入
                                bank_code = payee.get('bank_code', '')
                                bank_name_map = {"008": "華南商業銀行", "808": "玉山商業銀行", "700": "中華郵政"}
                                bank_name = bank_name_map.get(bank_code, "")
                                
                                frame.locator("input[name='BANKCODE']").first.evaluate(f"node => node.value = '{bank_code}'")
                                frame.locator("input[name='BANKNAME']").first.evaluate(f"node => node.value = '{bank_name}'")
                                
                                frame.locator("input[name='ACCNO']").first.fill(payee.get('account_number', ''))
                                
                                # 送出
                                new_page.once("dialog", _safe_accept)
                                frame.locator("input[name='Submit'][value='送出'], input[value='送出'], button:has-text('送出')").first.click(force=True)
                                time.sleep(2.0)
                                success_fill = True
                                break
                        
                        if success_fill:
                            new_page.close()
                        else:
                            return "ERROR: 無法在畫面上找到受款人填寫欄位 (可能未成功點選 '+' 按鈕)，請在開啟的視窗中手動操作。"
                    else:
                        return "ERROR: 無法在選單中找到 '受款人管理'，請在開啟的視窗中手動操作。"
                    
                    # 回到原來的 tab, 再次點擊儲存
                    if save_btn.count() > 0:
                        save_btn.first.click(timeout=5000)
                        time.sleep(1.5)
                except Exception as e:
                    return f"ERROR: 嘗試新增受款人資料時失敗: {str(e)}"

            # 移除對話框監聽器避免影響後續操作
            page.remove_listener("dialog", handle_dialog)

            # --- 6. 點擊「完成」 ---
            complete_btn = page.locator("input[type='submit'][value='完成'], input[type='button'][value='完成'], button:has-text('完成')")
            if complete_btn.count() > 0:
                try:
                    with page.expect_navigation(timeout=10000):
                        complete_btn.first.click(timeout=5000)
                    page.wait_for_load_state("networkidle")
                    return "OK - Payee added, saved, and clicked '完成'."
                except Exception as e:
                    return f"ERROR: 點擊了 '完成' 但導航失敗: {str(e)}"
            else:
                return "ERROR: 受款人已儲存，但找不到 '完成' 按鈕。"

        return "ERROR: 在畫面上找不到受款人代號 (mid) 的輸入框。"
    except Exception as e:
        return f"ERROR in add_payee: {str(e)}"


def print_receipt(page: Page) -> str:
    """Extracts the ASN from the 列印黏存單 form and stores the print page URL."""
    try:
        for frame in page.frames:
            # Try to extract ASN from the hidden input in the print form
            asn_input = frame.locator("form[action*='printmain'] input[name='asn'], form:has(input[value='列印黏存單']) input[name='asn']")
            if asn_input.count() > 0:
                asn = asn_input.first.get_attribute("value")
                if asn:
                    print_url = f"https://ntuacc.cc.ntu.edu.tw/acc/apply/printatt.asp?asn={asn}"
                    _state["print_url"] = print_url
                    print(f"🖨️ Print URL: {print_url}")
                    return f"OK - ASN={asn}, print URL saved."
        return "ERROR: Could not find ASN for print page."
    except Exception as e:
        return f"ERROR in print_receipt: {e}"


def dispatch_tool(page: Page, name: str, args: dict, payee: dict = None) -> str:
    match name:
        case "get_page_state":         return get_page_state(page)
        case "open_plan_expense_page": return open_plan_expense_page(page)
        case "submit_project_code":    return submit_project_code(page, args.get("project_code", ""))
        case "select_project_row":     return select_project_row(page, args.get("project_code", ""))
        case "add_invoice":            return add_invoice(page, args.get("invoice_number", ""), args.get("purpose", ""), args.get("amount", ""), args.get("is_last_invoice", True))
        case "add_payee":              return add_payee(page, args.get("payee_id", ""), args.get("note", ""), args.get("amount", ""), payee)
        case "print_receipt":          return print_receipt(page)
        case _:                        return "__TERMINAL__"

# ── Agent loop ────────────────────────────────────────────────────────────────

def build_initial_message(cfg: dict, payee: dict, invoices: list) -> str:
    inv_texts = []
    total_amount = 0
    for idx, inv in enumerate(invoices):
        items = inv.get("items", [])
        item_str = "; ".join(f"{i.get('name','')} x{i.get('qty',1)} @{i.get('price','?')}" for i in items)
        
        try:
            total_amount += int(float(inv.get('total_amount', 0)))
        except:
            pass

        inv_texts.append(f"""INVOICE {idx+1} (OCR, user-verified):
  invoice_number : {inv.get('invoice_number', 'NOT FOUND')}
  amount         : {inv.get('total_amount')}
  purpose        : {inv.get('expense_purpose')}
  items          : {item_str}""")

    inv_block = "\n\n".join(inv_texts)
    
    return f"""Fill in the NTU expense report with this data using your macro-tools:

PROJECT CODE: {cfg['project_code']}

{inv_block}
TOTAL AMOUNT OF ALL INVOICES: {total_amount}

PAYEE:
  payee_id       : {payee['payee_id']}
  note           : {payee.get('note_template', '')}

Begin with get_page_state()."""

def run_agent_loop(page: Page, llm: OpenAI, cfg: dict, payee: dict, invoices: list) -> str:
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user",   "content": build_initial_message(cfg, payee, invoices)},
    ]
    print("\n🤖 Agent starting...\n")

    no_tool_call_streak = 0

    for step in range(30):
        resp = llm.chat.completions.create(
            model=cfg["agent_model"],
            messages=messages,
            tools=TOOLS,
            tool_choice="auto",
            temperature=0,
            max_tokens=512,
        )
        msg = resp.choices[0].message
        messages.append(msg.model_dump(exclude_unset=True))

        if not msg.tool_calls:
            no_tool_call_streak += 1
            print(f"  💬 {msg.content}")

            recovered = _try_parse_fake_tool_call(msg.content or "")
            if recovered:
                name, args = recovered
                print(f"  ⚠️  Recovered tool call: {name}({args})")
                if name == "report_done":
                    return args.get("report_number", "")
                if name == "report_error":
                    print(f"\n❌ Agent error: {args.get('reason')}")
                    return ""
                
                result = dispatch_tool(page, name, args, payee)
                print(f"     → {result[:120]}")
                messages.append({"role": "user", "content": f"Tool result: {result}"})
                no_tool_call_streak = 0
                continue

            if no_tool_call_streak >= 3:
                raise RuntimeError("Agent failed to emit valid tool calls.")

            messages.append({"role": "user", "content": "Please respond ONLY with a tool call."})
            continue

        no_tool_call_streak = 0
        for tc in msg.tool_calls:
            name = tc.function.name
            args = json.loads(tc.function.arguments or "{}")
            print(f"  🔧 [{step+1}] {name}({json.dumps(args, ensure_ascii=False)})")

            if name == "report_done":
                rn = args.get("report_number", "unknown")
                print(f"\n✅ Done! Report: {rn}")
                return rn

            if name == "report_error":
                print(f"\n❌ Agent error: {args.get('reason')}")
                return ""

            result = dispatch_tool(page, name, args, payee)
            print(f"     → {result[:120]}")
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": result})

    print("⚠️  Max steps reached.")
    return ""


def _try_parse_fake_tool_call(text: str):
    match = re.search(r'\{[^{}]*"name"\s*:\s*"(\w+)"[^{}]*\}', text, re.DOTALL)
    if not match: return None
    try:
        obj = json.loads(match.group(0))
        name = obj.get("name")
        args = obj.get("parameters") or obj.get("arguments") or {}
        if name: return name, args
    except json.JSONDecodeError: pass
    return None

# ── Public entry points ───────────────────────────────────────────────────────

def auto_login(page: Page, cfg: dict) -> None:
    """背景自動執行台大報帳系統登入 (附帶自動除錯截圖與彈出視窗攔截)"""
    print("🔐 Executing automatic login...")
    page.goto("https://ntuacc.cc.ntu.edu.tw/acc/")
    page.wait_for_load_state("domcontentloaded")

    # 1. 監聽並攔截任何登入錯誤的彈出視窗 (例如 "帳號密碼錯誤")
    def handle_dialog(dialog):
        print(f"\n⚠️ 登入系統提示: {dialog.message}")
        dialog.accept()
    page.on("dialog", handle_dialog)

    # 2. 選擇身分並「強制觸發」舊版 JS 的 change() 函數
    id_select = page.locator("select#idtype")
    if id_select.count() > 0:
        id_select.select_option(value="5")
        try:
            # 強制執行網頁原始碼中的 change()，確保帳密欄位確實解除隱藏
            page.evaluate("if(typeof change === 'function') change();")
        except:
            pass
        time.sleep(0.5)

    # 3. 讀取帳號密碼
    username = cfg.get("ntu_username", "")
    password = cfg.get("ntu_password", "")
    print(f"   - Using username: {username}")
    print(f"   - Using password: {password}")
    if not username or not password:
        print("❌ 錯誤：在 config.json 中找不到 ntu_username 或 ntu_password")
        return

    # 4. 填寫帳號密碼
    page.locator("input#id").fill(username, force=True)
    page.locator("input#password").fill(password, force=True)

    # 5. 點擊登入按鈕

    # 5. 點擊登入按鈕
    login_btn = page.locator("input[type='submit'][name='actb'][value='登入']")
    if login_btn.count() > 0:
        try:
            with page.expect_navigation(timeout=10000):
                login_btn.first.click(timeout=5000)
            
            page.wait_for_load_state("networkidle")
            
            # 檢查網址是否如預期跳轉到內部系統
            if "secure.asp" in page.url or "index.asp" in page.url or "main.asp" in page.url:
                print("✅ 登入請求已送出，成功進入系統。")
            else:
                print(f"⚠️ 登入後網址未如預期改變，可能登入失敗，目前網址: {page.url}")
                page.screenshot(path="login_debug_after.png")
        except Exception as e:
            print(f"❌ 登入導航逾時或失敗: {e}")
            page.screenshot(path="login_debug_error.png")
    else:
        print("⚠️ 找不到登入按鈕。")

    # 移除對話框監聽器，避免影響後續正常的報帳流程
    page.remove_listener("dialog", handle_dialog)
    time.sleep(1.0)


# 修改原有的 run_submission 函數
def run_submission(cfg: dict, payee: dict, invoices: list, headless: bool = True) -> str:
    llm = OpenAI(base_url=cfg["agent_base_url"], api_key="not-needed")
    
    # Close any existing global session if running multiple times
    close_browser()
    
    pw = sync_playwright().start()
    browser = pw.chromium.launch(headless=True)
    context = browser.new_context()
    page = context.new_page()
    
    _state["pw"], _state["browser"], _state["page"] = pw, browser, page

    print("🌐 Opening NTU accounting system (background)...")
    
    # --- 替換掉原本的手動輸入，改為自動登入 ---
    auto_login(page, cfg)
    # ------------------------------------------

    report_number = run_agent_loop(page, llm, cfg, payee, invoices)
    _save_audit_log(report_number, invoices, payee)

    if report_number:
        print_url = _state.get("print_url")
        if print_url:
            # Save session state, close headless, open visible browser to print page
            storage = context.storage_state()
            browser.close()
            
            print("🖨️ Opening print page...")
            visible_browser = pw.chromium.launch(headless=False)
            visible_context = visible_browser.new_context(storage_state=storage)
            visible_page = visible_context.new_page()
            visible_page.goto(print_url)
            visible_page.wait_for_load_state("networkidle")
            
            _state["browser"], _state["page"] = visible_browser, visible_page
            print("✅ Agent finished. Print page is open.")
        else:
            print("✅ Agent finished. No print URL found.")
            close_browser()
    else:
        print("⚠️ Agent failed.")

    return report_number

_state = {"pw": None, "browser": None, "page": None}

def open_browser_for_login() -> None:
    pw = sync_playwright().start()
    browser = pw.chromium.launch(headless=False)
    page = browser.new_context().new_page()
    page.goto("https://ntuacc.cc.ntu.edu.tw/acc/")
    _state["pw"], _state["browser"], _state["page"] = pw, browser, page

def continue_after_login(cfg: dict, payee: dict, invoices: list) -> str:
    """Step B — called after the user confirms login is done in the browser window."""
    page = _state.get("page")
    if page is None:
        raise RuntimeError("No browser session open. Call open_browser_for_login() first.")

    # ── 在 Agent 啟動前，強制插入自動登入流程 ──
    # 這裡會讀取 cfg 中的帳號密碼，並執行你寫好的 auto_login
    print("\n--- 開始自動登入 ---")
    auto_login(page, cfg)
    print("--- 自動登入結束 ---\n")
    # ──────────────────────────────────────────

    llm = OpenAI(base_url=cfg["agent_base_url"], api_key="not-needed")
    report_number = run_agent_loop(page, llm, cfg, payee, invoices)
    _save_audit_log(report_number, invoices, payee)
    return report_number

def close_browser() -> None:
    if _state.get("browser"): _state["browser"].close()
    if _state.get("pw"): _state["pw"].stop()
    _state["pw"], _state["browser"], _state["page"] = None, None, None

def _save_audit_log(report_number: str, invoices: list, payee: dict) -> None:
    if not report_number: return
    
    inv_nums = [inv.get("invoice_number", "") for inv in invoices]
    try:
        total_amt = sum(int(float(inv.get("total_amount", 0))) for inv in invoices)
    except:
        total_amt = 0

    log = {
        "timestamp":      time.strftime("%Y-%m-%d %H:%M:%S"),
        "report_number":  report_number,
        "invoice_numbers": inv_nums,
        "total_amount":   total_amt,
        "payee_id":       payee["payee_id"],
        "payee_name":     payee["payee_name"],
    }
    log_path = f"audit_{time.strftime('%Y%m%d_%H%M%S')}.json"
    with open(log_path, "w", encoding="utf-8") as f:
        json.dump(log, f, ensure_ascii=False, indent=2)

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--session", default="session.json")
    parser.add_argument("--headless", action="store_true")
    args = parser.parse_args()

    cfg = load_config()
    with open(args.session, encoding="utf-8") as f:
        session = json.load(f)

    if "invoices" in session:
        run_submission(cfg, session["payee"], session["invoices"], headless=args.headless)
    else:
        # Fallback for old session.json
        run_submission(cfg, session["payee"], [session["invoice"]], headless=args.headless)