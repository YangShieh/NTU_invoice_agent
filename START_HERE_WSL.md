# Start the NTU Invoice Agent (Windows 11 + WSL)


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
