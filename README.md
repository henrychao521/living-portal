# Living Portal — Henry Living Tech 整合入口

> **公開網址：** [henrylivingtech.com](https://henrylivingtech.com)  
> **內網（Tailscale）：** `https://henrymac-studio.tail2562dd.ts.net:8443`

以 FastAPI 為核心閘道，透過單一入口整合三個獨立子系統，支援 iframe tab 切換。

---

## 架構總覽

```
                  ┌─────────────────────────────────────────┐
  瀏覽器          │           FastAPI Portal Gateway         │
  ──────────────► │  / (Portal HTML — tab 切換 + iframe)    │
                  │                                          │
  /rail/    ─────►│  StaticFiles → taiwan-rail-live/dist/   │
  /hydro/   ─────►│  StaticFiles → hydro-monitor/out/       │
  /taipei/  ─────►│  httpx Proxy → localhost:5555           │
                  └─────────────────────────────────────────┘
        ▲ :8080 (127.0.0.1 → Cloudflare Tunnel → henrylivingtech.com)
        ▲ :8443 (0.0.0.0   → Tailscale HTTPS)
```

### 子系統

| Tab | 路徑 | 技術棧 | 來源 repo |
|-----|------|--------|-----------|
| 🌊 北台灣水文監測 | `/hydro/` | Next.js 15 + Leaflet，`output: 'export'` | [hydro-monitor](https://github.com/henrychao521/hydro-monitor) |
| 🏙️ 台北即時看板 | `/taipei/` | Flask + 純 HTML/JS，httpx proxy | [taipei-dashboard](https://github.com/henrychao521/taipei-dashboard) |
| 🚂 台鐵即時地圖 | `/rail/` | React 19 + Vite + Leaflet | [taiwan-rail-live](https://github.com/henrychao521/taiwan-rail-live) |

---

## 快速啟動

### 前置條件

```bash
# Python（需 3.12+，建議 Homebrew）
/opt/homebrew/bin/python3 --version

# Node.js 20+
node --version

# 安裝 Python 依賴
pip install -r requirements.txt

# 台北看板獨立依賴（需另外安裝）
pip install flask feedparser requests
```

### 一鍵啟動

```bash
bash start.sh
```

`start.sh` 依序執行：
1. Build `taiwan-rail-live/frontend`（`npm run build`）
2. Build `hydro-monitor`（`BASE_PATH=/hydro npm run build`）
3. 啟動 `:8443` HTTPS 後端（Tailscale cert）
4. 啟動 `:8080` HTTP 後端（給 Cloudflare Tunnel）

台北看板需另行啟動：

```bash
/opt/homebrew/bin/python3 ~/Desktop/台北儀表板/taipei-dashboard/dashboard.py
# 監聽 localhost:5555
```

---

## 部署架構

```
henrylivingtech.com
      │
      ▼
Cloudflare Tunnel (cloudflared — macOS LaunchDaemon，開機自動啟動)
      │
      ▼
http://127.0.0.1:8080  ──►  FastAPI (hypercorn)
                                    │
                                    ├── /rail/    React 靜態檔案
                                    ├── /hydro/   Next.js 靜態檔案
                                    └── /taipei/  httpx proxy → :5555
```

- **Tailscale HTTPS**：`/Users/henry/Desktop/claude/henrymac-studio.tail2562dd.ts.net.{crt,key}`
- **Cloudflare**：cloudflared 安裝為 LaunchDaemon（`/Library/LaunchDaemons/`），開機自啟
- **HTTP/2**：hypercorn 原生支援

---

## 技術細節

### Sub-path 建置設定

**台鐵前端**（`frontend/vite.config.js`）：
```js
export default defineConfig({
  base: '/rail/',          // 所有 assets 參照 /rail/assets/...
  ...
})
```

**水文監測**（`next.config.ts`）：
```ts
basePath: process.env.BASE_PATH !== undefined
  ? process.env.BASE_PATH          // BASE_PATH=/hydro 時輸出 /hydro
  : (isProd ? '/hydro-monitor' : '')
```
建置指令：`BASE_PATH=/hydro npm run build`

**台北看板 proxy**（`backend/main.py`）：

Flask app 本身用相對路徑 `/api/...`，portal 透過 httpx 取 HTML 後做字串替換：
```python
html = html.replace("fetch('/api/",   "fetch('/taipei/api/")
html = html.replace('fetch("/api/',   'fetch("/taipei/api/')
html = html.replace('fetch(`/api/',   'fetch(`/taipei/api/')   # template literal
html = html.replace("? '/api/",       "? '/taipei/api/")
html = html.replace(": '/api/",       ": '/taipei/api/")
```

### Python 3.14 SSL 已知問題

`opendata.cwa.gov.tw`、`service119.tfd.gov.tw` 的 SSL 憑證缺少 Subject Key Identifier，Python 3.14 嚴格驗證下會拋出 `CERTIFICATE_VERIFY_FAILED`。

暫時解法：在 `dashboard.py` 的 `requests.get/post` 加 `verify=False`。

---

## 檔案說明

```
living-portal/
├── README.md           # 本文件
├── DEVLOG.md           # 完整開發歷程
├── start.sh            # 一鍵建置 + 啟動腳本
├── requirements.txt    # Python 依賴
└── backend/
    └── main.py         # FastAPI 整合閘道（portal HTML + 靜態掛載 + taipei proxy）
```

子系統原始碼分別在各自 repo，本 repo 僅存放整合層程式碼與文件。

---

## 相關連結

- [台鐵即時地圖](https://github.com/henrychao521/taiwan-rail-live) — 原始鐵路系統
- [北台灣水文監測](https://github.com/henrychao521/hydro-monitor) — 水文子系統
- [台北即時看板](https://github.com/henrychao521/taipei-dashboard) — 台北子系統
