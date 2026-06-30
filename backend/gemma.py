import torch
import base64
import json
import os
import re
import cv2
from openai import OpenAI
from PIL import Image
from pyzbar.pyzbar import decode
from qreader import QReader

# Optional: OpenCC for Simplified to Traditional Chinese conversion
try:
    from opencc import OpenCC
    cc = OpenCC('s2tw')
except ImportError:
    cc = None

qreader_engine = QReader()

COMMON_RULES = """
【通用嚴格約束】
6. items (品項列表): 
   - 提取 name (品名), qty (數量), price (單價)。
   - ⚠️ 捨棄貨幣符號：如果單價前面有 `$`、`NT$` 等符號，請務必完全捨棄，只保留純數字 (例如看到 `$510` 請直接輸出 `510`)。絕不可把 `$` 誤認為 `5` 或 `S`。
   - 完整提取：名稱必須「一字不漏」！包含所有中英文、標點符號與版本號。
   - 包含折扣明細：如果有「折扣」、「折價券」且帶有負數金額，務必視為獨立品項提取。
   - 行列對齊：請極度仔細對齊「每一行」的品名與單價。
7. total_amount (總計金額):
   - ⚠️ 只能輸出「純阿拉伯數字」(例如 1575)。必須強制捨棄任何 `$` 或 `NT$` 符號。
   - ⚠️ 捨棄貨幣符號：如果單價前面有 `$`、`NT$` 等符號，請務必完全捨棄，只保留純數字 (例如看到 `$510` 請直接輸出 `510`)。絕不可把 `$` 誤認為 `5` 或 `S`。
   - ⛔ 絕對排除陷阱：尋找官方的「總計」、「合計」或「應收金額」。【絕對禁止】抓取「實支」、「實收」、「找零」、「付現」旁邊的數字作為總金額。
   - 🧠 國字大寫精準轉換：如果圖片印有中文大寫金額，請務必仔細轉換為純數字。
     * 參考範例 1：「壹萬伍仟柒佰伍拾元整」 -> 15750
     * 參考範例 2：「參仟貳佰元整」 -> 3200
     * 參考範例 3：「捌佰零伍元」 -> 805
8. buyer_UBN (買方統編):
   - 請尋找畫面上的「買方統編」或「買受人統編」。
   - 如果有找到 8 位數字，請精準提取 (基本上是 "03734301")。
   - 如果畫面上完全沒有印買方統編，或者統編空白，請輸出 "NOT FOUND"。
9. confidence_score (辨識難度評分): 
   - 評估信心程度，根據辨識的難易度給出 1/2/3 其一分數分別對應弱中強。
10. ⚠️ 輸出格式絕對限制:
    - 回答只能包含合法的 JSON 物件。絕對禁止輸出任何解釋、問候語。
    - 不要使用 Markdown 區塊 (不要輸出 ```json)。
"""

PROMPTS = {
    "hand": """這是一張台灣的發票、收據或交易明細。請按照以下規則提取資訊，輸出為完整的 JSON 格式。
【提取規則】
1. invoice_number (發票號碼): 尋找 2 位大寫英文字母 + 8 位數字。如果找不到標準格式，請輸出 "NOT FOUND"。
2. date (交易日期): 西元格式 YYYY-MM-DD。
3. total_amount (總計金額): 
           - 【國字大寫絕對優先】：只要畫面上出現「中文大寫數字」(如：壹、貳、參...拾、佰、仟、萬)，請「一定」以該大寫金額為最終結果，並轉換為阿拉伯數字。
           - 【嚴格排除干擾字】：絕對不要抓取寫著「實收」、「收現」、「實支」、「找零」旁邊的數字，這些絕對不是總金額！
           -  請務必將視線移到發票的「最下方」，不少大寫國字在那裡，才是總金額的正確位置。
           - ⚠️ 【視覺防呆 - 尾部】：發票金額常以橫線結尾 (如 146.- 或 113.-)，請絕對忽略尾部的「-」或「元」，千萬不可以把它們當成「0」！(例如 2146.- 就是 2146，不是 21460)。
           - ⚠️ 【視覺防呆 - 頭部】：如果數字開頭帶有金錢符號「$」，絕對不可以誤認為數字「5」！(例如 $600 就是 600，不是 5600)。
           - 【邏輯驗證】：請確認你抓取的總計金額，必須「大於或等於」單一品項的價格。若無國字大寫，請尋找「總計」或「含稅」旁邊的最大數字。只能輸出純數字字串。
4. buyer_UBN (買方統編): 買方統編基本上為 "03734301"。
5. items (品項列表): 請盡可能辨識手寫品名。""" + COMMON_RULES,

    "einvoice": """這是一張台灣的電子發票。請嚴格遵守以下規則提取資訊：
【核心原則】只准看印刷字，絕對不要抓取手寫數字。
1. invoice_number (發票號碼): 2 個大寫英文字母 + 8 個數字。嚴禁抓取任何手寫號碼。
2. date (交易日期): 西元格式 YYYY-MM-DD。
3. total_amount (總計金額): 尋找印刷的「總計」。絕對封鎖手寫字。
4. buyer_UBN (買方統編): 買方統編基本上為 "03734301"。
5. items (品項列表): 書名通常很長且包含英文與特殊符號，請完整複製。""" + COMMON_RULES,

    "old": """這是一張台灣的傳統發票/收據。請按照以下規則提取資訊，輸出為完整的 JSON 格式。
【提取規則】
1. invoice_number (發票號碼): 尋找 2 位大寫英文字母 + 8 位數字。
2. date (交易日期): 西元格式 YYYY-MM-DD。
3. total_amount (總計金額): 尋找總計金額。如有國字大寫請轉為純阿拉伯數字。
4. UBN (統編): 買方統編固定為 "03734301"。
5. items (品項列表): 務必保留 ISBN、英文代碼、完整書名與標點符號。""" + COMMON_RULES
}

# ==========================================
# 2. Model Agent Class (vLLM API Connected)
# ==========================================
class HighFidelityInvoiceAgent:
    def __init__(self):
        # 🎯 重要：修改為 vLLM 伺服器正在對外提供服務的確切模型名稱
        self.model_id = "google/gemma-4-31b-it" 
        
        # 🎯 重要：修改為您轉發的 vLLM 連接埠 (通常為 8080 或 8081)
        vllm_port = 8080 
        
        print(f"🔗 正在建立連線至本地 vLLM 伺服器 (Port: {vllm_port}, 模型: {self.model_id})...")
        self.client = OpenAI(
            base_url=f"http://localhost:{vllm_port}/v1",
            api_key="none"  # vLLM 本地運行不需要真實的 OpenAI API key
        )
        print(f"✅ vLLM 代理端初始化成功！已卸載本地 VRAM 負擔。")

    def _encode_image_to_base64(self, image_path):
        """將本地圖片編碼為 base64 字串以適應 vLLM 的 OpenAI 視覺格式要求"""
        mime_type = "image/jpeg"
        if image_path.lower().endswith(".png"):
            mime_type = "image/png"
        elif image_path.lower().endswith(".webp"):
            mime_type = "image/webp"

        with open(image_path, "rb") as image_file:
            base64_str = base64.b64encode(image_file.read()).decode("utf-8")
            
        return f"data:{mime_type};base64,{base64_str}"

    def classify_invoice(self, image_path):
        image_data_url = self._encode_image_to_base64(image_path)
        classifier_prompt = "請觀察這張發票特徵，並嚴格分類為: 'einvoice' (電子發票), 'old' (傳統印刷發票) 或 'hand' (手寫收據)。只輸出代號。"
        
        response = self.client.chat.completions.create(
            model=self.model_id,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": classifier_prompt},
                        {"type": "image_url", "image_url": {"url": image_data_url}}
                    ]
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

    def extract_invoice_data(self, image_path, prompt):
        image_data_url = self._encode_image_to_base64(image_path)
        
        print("    ⏳ 模型正在深度萃取中 (請觀看下方即時輸出)...")
        
        # 利用 vLLM 的 Stream 傳輸功能重現即時字符流效果
        stream = self.client.chat.completions.create(
            model=self.model_id,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": image_data_url}}
                    ]
                }
            ],
            max_tokens=1500,
            temperature=0.0,
            stream=True
        )
        
        output_chunks = []
        for chunk in stream:
            if chunk.choices[0].delta.content is not None:
                content = chunk.choices[0].delta.content
                print(content, end="", flush=True)
                output_chunks.append(content)
        print() # 流式傳輸結束後換行
        
        output_text = "".join(output_chunks)
        if cc: 
            output_text = cc.convert(output_text)
        return output_text

# ==========================================
# 3. Utilities & Deterministic OCR
# ==========================================
def scan_taiwan_einvoice_qr(image_path):
    """使用 AI (QReader) 強制定位並讀取台灣電子發票 QR Code"""
    try:
        image = cv2.cvtColor(cv2.imread(image_path), cv2.COLOR_BGR2RGB)
        decoded_texts = qreader_engine.detect_and_decode(image=image)
        
        for qr_text in decoded_texts:
            if qr_text is None:
                continue
                
            if len(qr_text) > 50 and re.match(r"^[A-Z]{2}\d{8}", qr_text):
                roc_year = int(qr_text[10:13])
                month = qr_text[13:15]
                day = qr_text[15:17]
                west_year = roc_year + 1911
                
                # --- NEW CODE HERE ---
                # Extract Buyer UBN. If it's all zeros, it means no UBN was given.
                raw_buyer_ubn = qr_text[53:61].strip()
                buyer_ubn = "NOT FOUND" if raw_buyer_ubn == "00000000" else raw_buyer_ubn
                
                return {
                    "invoice_number": qr_text[0:10],
                    "date": f"{west_year}-{month}-{day}",
                    "total_amount": str(int(qr_text[29:37], 16)),
                    "seller_UBN": qr_text[45:53],
                    "buyer_UBN": buyer_ubn # <-- Add this to the return dictionary
                }
                
    except Exception as e:
        print(f"    ⚠️ QR Code 解析發生錯誤: {e}")
        
    return None

def clean_json_output(raw_text):
    """強力清理 VLM 輸出的文字，擷取最外層的 { } 範圍，無視任何廢話。"""
    cleaned = raw_text.strip()
    match = re.search(r'\{.*\}', cleaned, re.DOTALL)
    
    if match:
        json_str = match.group(0)
        json_str = json_str.replace("```json", "").replace("```", "")
        return json_str.strip()
    
    return cleaned.replace("```json", "").replace("```", "").strip()

def validate_and_clean_data(invoice_data):
    invoice_num = str(invoice_data.get("invoice_number", ""))
    if invoice_num != "NOT FOUND":
        clean_invoice_num = re.sub(r'[^A-Za-z0-9]', '', invoice_num).upper()
        if not re.match(r"^[A-Z]{2}\d{8}$", clean_invoice_num):
            invoice_data["invoice_number"] = "NOT FOUND"
        else:
            invoice_data["invoice_number"] = clean_invoice_num
    return invoice_data

# ==========================================
# 4. Main Flow
# ==========================================
if __name__ == "__main__":
    SOURCE_FOLDER = "./invoices"       
    OUTPUT_FOLDER = "./invoices_gemmas3"  
    
    if not os.path.exists(SOURCE_FOLDER):
        print(f"❌ 錯誤：找不到來源資料夾 {SOURCE_FOLDER}。請創建並放入圖片。")
        exit()

    os.makedirs(OUTPUT_FOLDER, exist_ok=True)
    agent = HighFidelityInvoiceAgent()
    files = os.listdir(SOURCE_FOLDER)
    image_files = [f for f in files if f.lower().endswith(('.png', '.jpg', '.jpeg', '.bmp', '.webp'))]
    
    total_processed = 0
    qr_success_count = 0

    print("\n" + "=" * 50)
    print(f"📁 在 {SOURCE_FOLDER} 找到 {len(image_files)} 張發票，開始自動分類與萃取...")
    print("=" * 50)

    for index, filename in enumerate(image_files):
        image_path = os.path.join(SOURCE_FOLDER, filename)
        base_name = os.path.splitext(filename)[0]
        json_path = os.path.join(OUTPUT_FOLDER, f"{base_name}.json")

        if os.path.exists(json_path): continue

        print(f"\n📄 [{index+1}/{len(image_files)}] 正在處理: {filename}...")
        
        try:
            invoice_type = agent.classify_invoice(image_path)
            print(f"    🔎 分類結果: [{invoice_type.upper()}]")
            
            qr_data = None
            if invoice_type == "einvoice":
                print("    🔍 正在嘗試掃描 QR Code...")
                qr_data = scan_taiwan_einvoice_qr(image_path)
                if qr_data:
                    print("    🎯 成功從 QR Code 完美提取核心資訊！")
                    print(f"       >> 📄 號碼: {qr_data['invoice_number']}")
                    print(f"       >> 📅 日期: {qr_data['date']}")
                    print(f"       >> 💰 金額: {qr_data['total_amount']}")
                    print(f"       >> 🏢 賣方統編: {qr_data.get('seller_UBN', 'N/A')}")
                    qr_success_count += 1
                else:
                    print("    ⚠️ QR Code 掃描失敗，將完全依賴 VLM 辨識。")

            task_prompt = PROMPTS[invoice_type]
            raw_response = agent.extract_invoice_data(image_path, task_prompt)
            clean_text = clean_json_output(raw_response)
            
            try:
                invoice_data = json.loads(clean_text)
                invoice_data["filename"] = filename
                invoice_data["detected_type"] = invoice_type
                
                if qr_data:
                    invoice_data["invoice_number"] = qr_data["invoice_number"]
                    invoice_data["date"] = qr_data["date"]
                    invoice_data["total_amount"] = qr_data["total_amount"]
                    invoice_data["qr_verified"] = True
                else:
                    invoice_data["qr_verified"] = False

                invoice_data = validate_and_clean_data(invoice_data)
                
                confidence = invoice_data.get("confidence_score", "N/A")
                print(f"    🧠 VLM 辨識信心分數: {confidence}/100")

            except json.JSONDecodeError:
                print("    ⚠️ JSON 解析失敗，儲存原始文字以便除錯")
                invoice_data = {"filename": filename, "error": "JSONDecodeError", "raw_output": clean_text, "qr_verified": False}

            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(invoice_data, f, indent=4, ensure_ascii=False)
            
            if invoice_data.get("qr_verified"):
                print(f"    💾 已儲存: {base_name}.json 🟩 [QR Code 校正]")
            else:
                print(f"    💾 已儲存: {base_name}.json 🟧 [純 VLM 辨識]")
                
            total_processed += 1

        except Exception as e:
            print(f"    ❌ 處理失敗: {e}")

    print("\n" + "=" * 50)
    print(f"🎉 所有自動分類與 OCR 任務全部完成！")
    print(f"📊 總計處理: {total_processed} 張")
    print(f"📱 QR Code 成功救援: {qr_success_count} 張 ({(qr_success_count/max(1, total_processed))*100:.1f}%)")
    print("=" * 50)