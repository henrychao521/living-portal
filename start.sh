#!/bin/bash
set -e

CERT=/Users/henry/Desktop/claude/henrymac-studio.tail2562dd.ts.net.crt
KEY=/Users/henry/Desktop/claude/henrymac-studio.tail2562dd.ts.net.key
HYDRO_DIR="/Volumes/128G/hydro-monitor"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# ── Build frontends ───────────────────────────────────────────────────────────
echo "[1/3] Building rail frontend..."
cd "$SCRIPT_DIR/frontend"
npm run build
echo "      Done → frontend/dist/"

echo "[2/3] Building hydro-monitor..."
cd "$HYDRO_DIR"
BASE_PATH=/hydro npm run build
echo "      Done → hydro-monitor/out/"

# ── Start backend ─────────────────────────────────────────────────────────────
echo "[3/4] Starting backend on :8443 (HTTPS / Tailscale)..."
cd "$SCRIPT_DIR/backend"
RUN_BACKGROUND=0 /opt/homebrew/bin/python3 -m hypercorn main:app \
  --bind 0.0.0.0:8443 \
  --keyfile "$KEY" \
  --certfile "$CERT" \
  --log-level warning &
HTTPS_PID=$!

echo "[4/4] Starting backend on :8080 (HTTP / Cloudflare Tunnel)..."
/opt/homebrew/bin/python3 -m hypercorn main:app \
  --bind 127.0.0.1:8080 \
  --log-level warning &
HTTP_PID=$!

echo "      HTTPS PID=$HTTPS_PID  HTTP PID=$HTTP_PID"
echo "      Tailscale → https://henrymac-studio.tail2562dd.ts.net:8443"
echo "      Public    → https://henrylivingtech.com (via Cloudflare)"
echo ""
echo "Press Ctrl-C to stop both backends."
wait $HTTPS_PID $HTTP_PID
