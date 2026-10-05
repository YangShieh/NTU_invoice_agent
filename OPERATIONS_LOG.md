# 操作異常紀錄

系統會以 JSON Lines 格式留下不含個資的診斷紀錄：

- `frontend/logs/operations-YYYY-MM-DD.jsonl`：上傳、OCR 回傳、人工確認、Agent 操作及列印結果。
- `backend/logs/ocr-YYYY-MM-DD.jsonl`：影像分類、QR 辨識、模型萃取及 JSON 解析結果。

同一案件以 `case_id` 串接前後端事件。紀錄包含階段（`stage`）、結果（`outcome`）、耗時、錯誤類型及程式堆疊位置，方便統計最常失敗的環節。
發生錯誤時，畫面訊息也會顯示案件 ID，使用者回報該 ID 即可對照紀錄。

姓名、身分證字號、銀行帳號、密碼、發票號碼、品項內容與原始模型輸出均不寫入操作紀錄。紀錄預設保留 30 天；使用者執行「清除個人資料」時，診斷紀錄仍會保留，因為其中沒有案件個資。

常用查詢範例：

```bash
# 最近的失敗
rg '"outcome": "failed"' frontend/logs backend/logs

# 追查某一案件
rg '案件的 case_id' frontend/logs backend/logs
```
