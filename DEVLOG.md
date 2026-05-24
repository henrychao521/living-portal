# DEVLOG — Henry Living Tech Portal

整個系統從單一鐵路地圖成長為整合三個子系統的即時資訊入口，完整開發歷程如下。

---

## 台鐵即時地圖（taiwan-rail-live）

### v0.1 — 基礎地圖 + 即時列車

**時間**：2025 早期

- React + Leaflet 深色地圖（OSM tile + CSS filter 反色）
- FastAPI + TDX token 管理（OAuth client credentials）
- `/api/trains` 每 30 秒輪詢 TDX TrainLiveBoard
- TrainMarker：依車種上色（自強 cyan、莒光 yellow、區間 green）

### v0.2 — 車站資訊 + 景點整合

- `/api/stations`：靜態車站清單，SQLite 24h 快取
- `/api/attractions`：TDX Tourism API，Haversine 過濾附近景點
- AttractionToast：列車附近景點跑馬燈（10s 輪詢切換）

### v0.3 — TripPlanner 班車查詢

**三階段流程**：search（車種 + 終點）→ trains（班次清單）→ route（停靠站 + 天氣 + 景點）

新增後端 endpoint：
- `GET /api/station/{id}/types`：未來 120 分鐘內可搭車種 + hasTripLine
- `GET /api/trips`：`asyncio.Semaphore(3)` 並行取 DailyTimetable，回傳停靠站

地圖路線：`routeStops` → Polyline + CircleMarker（端點 r=8 實心，中間 r=5 空心）

`FitBounds` 元件自動 fitBounds 路線範圍。

車站 marker 改用 `<Pane name="stations" style={{zIndex:640}}>` 解決點擊被 TrainMarker 攔截。

### v0.4 — 定位功能

- 瀏覽器 Geolocation API + JS Haversine 找最近車站
- 定位鈕移至頂欄（`◎ 定位查詢`），不被面板遮住

### v0.5 — 行動裝置 UX 優化

**效能：**
- JS bundle：402 KB → 125 KB gzip（code splitting：vendor / map / index 三包）
- HTTP/2：uvicorn → hypercorn

**快取策略：**
- `/assets/*` → `Cache-Control: immutable`
- `index.html` → `no-cache`

**手機 UX：**
- 面板改為底部拉起（72 vh bottom sheet）
- ZoomControl 移至右下角
- AttractionToast：`panelOpen` prop，面板開啟時自動隱藏
- 附近車站快選：終點搜尋空白時顯示最近 6 站（含距離）
- iOS safe area：`padding-top: env(safe-area-inset-top)`
- OnboardingModal：每日首次顯示操作說明，「今日不再顯示」存 localStorage

### v0.6 — 公開部署 + 啟動腳本

**Cloudflare Tunnel：**
- cloudflared 安裝為 macOS LaunchDaemon（`/Library/LaunchDaemons/com.cloudflared.plist`）
- 開機自動啟動，Tunnel 指向 `http://127.0.0.1:8080`

**後端雙埠：**
- `:8443` HTTPS（Tailscale cert）→ `https://henrymac-studio.tail2562dd.ts.net:8443`
- `:8080` HTTP（僅 127.0.0.1，給 cloudflared）→ `https://henrylivingtech.com`

**start.sh** 同時啟動兩個 hypercorn 實例，`wait $PID` 管理生命週期。

HTTP/2 via Cloudflare 驗證：`cf-ray: a004c8062c4b4a24-TPE`（台北節點）

---

## 北台灣水文監測（hydro-monitor）

**技術棧：** Next.js 15 + Leaflet + React Query

- 即時水位、流量、雨量站資料（政府開放資料）
- Leaflet 地圖上的水文站 marker，顯示即時數值
- Next.js `output: 'export'` 靜態輸出，可直接 serve HTML

**sub-path 設定：**
```ts
// next.config.ts
basePath: process.env.BASE_PATH !== undefined
  ? process.env.BASE_PATH
  : (isProd ? '/hydro-monitor' : '')
```
整合時用 `BASE_PATH=/hydro npm run build` 輸出到 `/hydro/` 路徑下。

---

## 台北即時資訊看板（taipei-dashboard）

**技術棧：** Flask + 純 HTML/CSS/JS（單一 `dashboard.py` 檔案）

**功能卡片：**
- 🌤️ 天氣（CWA 即時觀測）
- 🌀 地震（CWA 最近地震 + Leaflet 地圖）
- 🚒 消防 119（台北市消防局即時出動紀錄）
- 📰 新聞（Google News RSS via feedparser，支援多區域切換）

**後來移除：** Meshtastic 無線電節點監控（功能不穩定，介面複雜度高）

**SSL 問題（Python 3.14）：**

`opendata.cwa.gov.tw`、`service119.tfd.gov.tw` 的憑證缺少 Subject Key Identifier，Python 3.14 嚴格驗證下失敗。

解法：`requests.get(url, verify=False)`（內部資料抓取，可接受風險）

---

## v0.7 — Living Portal 整合

**整合日期：** 2026-05-24

### 目標

將三個獨立系統整合為單一入口，使用 tab 切換不同子系統，對外只需記一個 URL。

### 方案選擇

**iframe 方案（採用）**：每個子系統保持完全獨立，portal 只做 tab 切換 + iframe src 管理。優點：各系統不需改架構，WebSocket / 輪詢繼續正常運作。

**SPA 合併（放棄）**：需要大量重構，子系統的路由和狀態管理衝突難以解決。

### 實作細節

**前端（portal HTML 嵌在 main.py 常數中）：**
```html
<nav id="nav">
  <button class="tab active" onclick="show('hydro',this)">🌊 北台灣水文</button>
  <button class="tab" onclick="show('taipei',this)">🏙️ 台北看板</button>
  <button class="tab" onclick="show('rail',this)">🚂 台鐵即時</button>
</nav>
<iframe id="hydro" src="/hydro/" class="active"></iframe>
<iframe id="taipei" src="/taipei/"></iframe>
<iframe id="rail" src="/rail/"></iframe>
```

**sub-path routing（FastAPI）：**
```python
app.mount('/hydro', StaticFiles(directory=HYDRO_DIST, html=True))
app.mount('/rail',  StaticFiles(directory=FRONTEND_DIST, html=True))

@app.get('/taipei/')
async def taipei_home():
    resp = await client.get('http://localhost:5555/')
    html = resp.text
    # 改寫 JS 中的 /api/ 路徑
    html = html.replace("fetch(`/api/", "fetch(`/taipei/api/")
    ...
    return HTMLResponse(html)
```

**踩坑紀錄：**

1. **ServePrecompressedMiddleware 路徑**：原本判斷 `/assets/` 前綴，改掛到 `/rail` 後需改為 `/rail/assets/`，否則 gzip 不生效。

2. **Next.js `next.config.ts` 本地版本落後**：本地的 `basePath` 寫死為 `/hydro-monitor`，而 GitHub 上的版本已有 `BASE_PATH` 環境變數邏輯。本地未同步導致 `BASE_PATH=/hydro npm run build` 無效。解法：手動對齊 GitHub 版本。

3. **台北看板 fetch template literal 未被改寫**：`fetch(\`/api/news?region=${r}\`)` 使用反引號，原本的替換規則只涵蓋單引號和雙引號。補上 `html.replace('fetch(\`/api/', 'fetch(\`/taipei/api/')` 後修復。

4. **feedparser 未安裝**：`pip install feedparser --break-system-packages`（Python 3.14 系統環境限制）。

---

## v0.8 — 班表精確化 + 景點資訊補強 + 水文 v0.5 整合

**整合日期：** 2026-05-24

### 班車查詢根本修正

**問題：** 臺北→臺中 顯示「此時段無直達班次」，即使明明有班次。

**根因：** `StationLiveBoard` 只回傳目前「在站」或「即將進站」的列車，時間視窗極窄（約 10–30 分鐘）。早上 8:30 查詢，`StationLiveBoard` 僅有 4 筆，3 小時內的班次完全不在內。

**解法：** `/api/trips` 與 `/api/station/types` 改用 `DailyTrainTimetable/Today`（今日完整班表）：
```python
# OData filter：取得所有停靠該站的班次
timetables = await TDX.get_station_timetable_today(from_station)
# StationLiveBoard 保留，僅用於疊加延誤 / 月台（best-effort）
delay_map    = {b['TrainNo']: b['DelayTime'] for b in liveboard}
platform_map = {b['TrainNo']: b['Platform']  for b in liveboard}
```

**陷阱：** `DailyTrainTimetable/Today/StationNo/{id}` → 404。`GeneralTimetable/StationNo/{id}` → 404。
正確 OData 路徑：`/v3/Rail/TRA/DailyTrainTimetable/Today` + `$filter=StopTimes/any(s: s/StationID eq '{id}')`

### 跨午夜班次（v0.9，已於 v1.0 修正）

原設計：晚上 21:00 後合併隔天班表。但實測確認 TDX `DailyTrainTimetable/TrainDate/{date}` 回傳 404（僅支援 `Today`），此功能在 v1.0 改以今日班表近似明日。

### TripPlanner 起始站選擇

附近車站晶片從「終點站快選」改為「起始站選擇」：
- `selectedOrigin` state（預設為點擊的車站）
- 雙輸入框各有獨立搜尋下拉
- active 晶片以 `color: #00d4ff` 高亮顯示
- 車種選單隨起始站改變重新載入

### 景點資訊補強

**問題：** TDX API 對約半數景點的 description 填入字面值「無」。

**解法：**
1. `tdx.py`：過濾 `{'無', '—', '-', ''}` 描述，改存 `''`；新增 `address`（地址）和 `open_time`（開放時間）欄位
2. `database.py`：
   - `tourism_spots` 加 `address`、`open_time`、`wiki_description` 三欄（migration 自動清舊資料）
   - `upsert_tourism` 改為 UPSERT（`ON CONFLICT DO UPDATE`），保留已補充的 `wiki_description`
   - `get_tourism_near` 回傳 `description or wiki_description`
3. `TripPlanner.jsx`：description 空白時 fallback 顯示地址；另一行顯示開放時間
4. Background task `_wikipedia_enrich()`：每日對空描述景點查 `zh.wikipedia.org/api/rest_v1/page/summary/{name}`，限速 1 req/s
5. `PREFETCH_CITIES` 補上苗栗（MiaoliCounty）、雲林（YunlinCounty）

**Next.js build 路徑修正（關鍵 bug）：**
- `BASE_PATH=""` → Next.js 生成 `/_next/...` 絕對路徑
- Portal 掛 `/hydro/`，瀏覽器找 `/_next/` → 503
- 修正：`BASE_PATH="/hydro" npm run build` → 生成 `/hydro/_next/...` ✓
- `start.sh` 早已正確寫 `BASE_PATH=/hydro npm run build`，問題在之前 build 時手動誤用 `BASE_PATH=""`

### scraper 覆蓋率擴大

```python
# 前：
for s in stations[:50]:
    for spot in get_tourism_near(s['lat'], s['lon'], 2000)[:2]:
# 後：
for s in stations:            # 全 245 站
    for spot in get_tourism_near(s['lat'], s['lon'], 2000)[:3]:  # 每站 3 個
```

### 水文系統 v0.5 整合

GitHub pull 44 commits（`3c69615` → `f95a49f`）：

| 功能 | 說明 |
|------|------|
| 全台水文分區 | 全台 / 北台灣 / 中部 / 中南部 / 南部 / 東部 / 離島 |
| 水位站擴充 | 91 站全台即時水位 |
| 水庫擴充 | 8 座水庫（石岡壩 / 德基 / 日月潭等） |
| AQI | 右側面板即時空氣品質，含 PM2.5 |
| CCTV 擴充 | 全台水利署監控鏡頭，點擊 popup 顯示即時影像 |
| 圖層切換 | Header 雨量 / 水位 / 水庫 / AQI / 鏡頭 各自開關 |
| 輪詢頻率 | 5 分鐘 → 1 分鐘 |

---

## v1.0 — 班車查詢除錯 + 明日班次顯示

**日期：** 2026-05-24

### 問題一：站名搜尋 台 vs 臺 字元不符

**症狀：** 輸入「台南」、「台中」等地名，搜尋下拉無建議、目的站無法選取，查詢按鈕永遠 disabled，造成「無班次」假象。

**根因：** TDX 車站 API 回傳名稱為繁體「臺」（臺北、臺南、臺中、臺東），使用者輸入的是常用字「台」，`String.includes()` 嚴格字元比對失敗。

**解法：** `TripPlanner.jsx` 加一行正規化函式，搜尋前將「台」替換為「臺」：
```js
const normalize = q => q.replace(/台/g, '臺')
// filteredDests 改為 s.name.includes(normalize(destQuery))
```

**影響站名：** 臺北（1000）、臺南（4220）、臺中（3300）、臺東（6000）、臺中港（2210）、臺北-環島（1001）

### 問題二：今日班次已結束，查詢回傳空白無提示

**診斷過程：**

```python
# 直接呼叫 TDX 確認資料存在
timetables = await TDX.get_station_timetable_today('1000')
# → 333 筆，其中 29 班停靠臺南（4220）

# 逐步追蹤 api_trips 過濾結果
no from_idx: 0
has from_idx, no to_idx: 304   # 304 班車不經過臺南
filtered by time window: 29    # 29 班有臺北→臺南，但全在視窗外
PASSED: 0
```

**根因：** 測試時間 20:02，臺北→臺南最後一班 19:30（32 分前），3 小時視窗（-5 ~ +180 分）剛好全部落空。班次資料正確，視窗問題。

**TDX 端點限制確認：**
- `DailyTrainTimetable/TrainDate/{YYYYMMDD}` → **404**（僅支援 Today）
- `GeneralTimetable`（帶或不帶 filter）→ **404**
- 可用端點：`DailyTrainTimetable/Today` + OData `$filter=StopTimes/any(s: s/StationID eq '{id}')`

**解法：** 今日無班次且 `hour >= 14` 時，重用今日班表（TRA 日常固定，近似明日），取最早 8 班標記 `nextDay=True`，前端顯示「── 明日最早班次 ──」分隔線。

**後端（main.py）：**
```python
def _extract_trips(timetables, from_station, to_station, train_type, trip_line,
                   now, time_filter=True, ..., next_day=False):
    # 抽出共用邏輯，time_filter=False 時不限時間視窗
    ...

# 今日無班次 fallback
if not result and now.hour >= 14:
    result = _extract_trips(timetables, ..., time_filter=False, next_day=True)[:8]
```

**前端（TripPlanner.jsx）：**
```jsx
{showDivider && <div className="tp-day-divider">── 明日最早班次 ──</div>}
{t.nextDay
  ? <span className="tp-badge tp-next-day">明日</span>
  : <MinuteBadge depart={t.depart} delay={t.delay} />
}
```

---

## TDX API 欄位備忘

| API | 欄位 | 格式 | 備注 |
|-----|------|------|------|
| StationLiveBoard | `ScheduleDepartureTime` | `HH:MM:SS` → `[:5]` | 注意：無末尾 `d`（不是 Scheduled） |
| StationLiveBoard | `ScheduleArrivalTime` | `HH:MM:SS` → `[:5]` | 發車 / 到站二選一 |
| DailyTrainTimetable StopTimes | `ArrivalTime` / `DepartureTime` | `HH:MM` | 已 5 字，不需截切 |
| StationLiveBoard | `TripLine` | `0/1/2` | 0=不限, 1=山線, 2=海線 |
| TrainLiveBoard | `StationID` | 字串 | 無 GPS，需從 stations DB lookup lat/lon |

**已確認 404 的端點（勿使用）：**
- `DailyTrainTimetable/TrainDate/{YYYYMMDD}` — 僅支援 `/Today`
- `DailyTrainTimetable/Today/StationNo/{id}` — 用 OData `$filter` 取代
- `GeneralTimetable`（帶或不帶 filter）— v3 不存在此資源

---

## 環境設定備忘

```
Python   /opt/homebrew/bin/python3  (3.14 + OpenSSL 3.6.2)
                                    系統 Python 用 LibreSSL，無 TLS 1.3

Tailscale cert  ~/Desktop/claude/henrymac-studio.tail2562dd.ts.net.{crt,key,fullchain.pem}

cloudflared     macOS LaunchDaemon → /Library/LaunchDaemons/com.cloudflared.plist
                開機自啟，Tunnel 指向 127.0.0.1:8080

Port 8080       HTTP，僅 127.0.0.1（Cloudflare Tunnel）
Port 8443       HTTPS，0.0.0.0（Tailscale + 區域網路）
Port 5555       台北看板 Flask app（本機內部，不對外）
```
