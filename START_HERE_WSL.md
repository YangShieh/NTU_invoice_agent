# Start the NTU Invoice Agent (Windows 11 + WSL)

## 一鍵啟動（建議）

進入 `NTU_invoice_agent_win11` 資料夾，直接雙擊：

```text
START_WIN11.bat
```

啟動器會依序：

1. 在 WSL 啟動 vLLM，等待 `8080` 連接埠。
2. 啟動 OCR API，等待 `8000` 連接埠。
3. 啟動前端伺服器，等待 `8001` 連接埠。
4. 自動開啟 Electron 報帳畫面。

啟動器視窗必須保持開啟。vLLM、OCR API、前端或 Electron 若意外關閉，會在數秒後自動重啟；關閉 Electron 視窗後也會再次開啟。

預設使用：

- WSL：`Ubuntu-22.04`
- 後端 Conda 環境：`ntu-invoice-vllm`
- 前端 Conda 環境：`invoice_frontend`

若名稱不同，可從 PowerShell 手動指定：

```powershell
powershell -ExecutionPolicy Bypass -File .\win11_supervisor.ps1 `
  -WslDistro Ubuntu-22.04 `
  -BackendCondaEnv ntu-invoice-vllm `
  -FrontendCondaEnv invoice_frontend
```

以下三個 Terminal 步驟保留作為手動啟動／除錯方式。


開啟 **POWERSHELL** 方法:
1. win +r
2. 輸入 powershell
3. cd Desktop/NTU_invoice_agent

需要三個powershell:
1. vLLM AI server
2. Invoice extraction API
3. User interface and automation agent－學生輸入的畫面



## Every time you start the application

### Terminal 1 — start vLLM


```bash
cd backend
wsl -d Ubuntu-22.04
conda activate ntu-invoice-vllm
./vllm_4bit.sh
```

### Terminal 2 — start the invoice API

Open a second Ubuntu terminal and run:

```bash
cd backend
wsl -d Ubuntu-22.04
conda activate ntu-invoice-vllm
python api.py
```

### Terminal 3 — start the user interface

Open a third Ubuntu terminal and run:

```bash
cd electron
npm start
```


## Correct startup order

Always start them in this order 好了才能下一個:

1. vLLM, then wait until port 8080 is ready.
2. API, then wait until port 8000 is ready.
3. User interface, then open port 8001 in the browser.

## 注意事項
1. 這幾個都不能關powershell
2. 如果把視窗３跑出來的畫面關了，就要重新再開Terminal 3，初始畫面應該會在中間有個“希望”按鈕
3. frontend/config.json裡面有帳號密碼跟計劃代號
