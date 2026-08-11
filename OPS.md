# 維運手冊（Mac Studio 上執行）

正式服務跑在 **Mac Studio**（henrymac-studio，Tailscale 100.117.208.54），本 repo 在其他機器上只是工作副本（.env 憑證僅存在 Studio）。

## 套用 2026-08-11 修正（重啟一次即可）

```bash
cd ~/living-portal   # 依 Studio 實際路徑
git pull
# 停舊的兩個 hypercorn
pkill -f "hypercorn main:app"
./start.sh
```

本次修正重點：CWA 金鑰不再硬編碼（一律讀 .env）、**8443 實例不再重複跑背景任務**（修 Telegram 雙重通知與 API 額度加倍）、班次查詢合併 v1.0 邏輯（修「此時段無直達班次」）、地震警報改取近 10 筆最大者、screenshot 路徑不外洩。

## CWA 金鑰換發（外洩處置，盡快）

舊金鑰 `CWA-21F97E17-…` 已隨公開 repo 歷史外洩。步驟：
1. 到 CWA 氣象開放資料平台會員中心重新產生 API 授權碼
2. Studio 上編輯 `living-portal/.env` 的 `CWA_API_KEY=新金鑰`
3. `pkill -f "hypercorn main:app" && ./start.sh`
4. hydro-monitor 若另有部署也用同一支金鑰，一併更新其環境

## 已知待辦（審查發現，未套用）

- TLS `verify=False`（CWA 憑證 workaround）→ 建議改 truststore：Studio 上 `pip3 install truststore --break-system-packages` 後在 alerts.py/weather.py 換掉
- tdx/weather 快取無上限、naive datetime、scraper SSRF 白名單——完整清單見 2026-08-11 審查紀錄
- 若要讓外部機器代管維運：Studio 開「系統設定 → 一般 → 共享 → 遠端登入」，Tailscale SSH 即可進
