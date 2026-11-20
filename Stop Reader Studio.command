#!/bin/zsh
# ==============================================================================
# Reader Studio v3.3 — Graceful Stop Script
# ==============================================================================
PORT="${PORT:-7880}"
PIDS="$(lsof -Pi :$PORT -sTCP:LISTEN -t 2>/dev/null || true)"

echo "============================================================"
echo " Stopping Reader Studio on port $PORT..."
echo "============================================================"

if [[ -z "$PIDS" ]]; then
    echo "Reader Studio is not currently running on port $PORT."
    sleep 1.5
    exit 0
fi

kill ${(f)PIDS} >/dev/null 2>&1 || kill $PIDS >/dev/null 2>&1 || true

for i in {1..20}; do
    if ! lsof -Pi :$PORT -sTCP:LISTEN -t >/dev/null 2>&1 ; then
        echo "[OK] Reader Studio stopped cleanly."
        sleep 1.5
        exit 0
    fi
    sleep 0.25
done

echo "[!] Forcing shutdown on remaining process..."
REMAINING="$(lsof -Pi :$PORT -sTCP:LISTEN -t 2>/dev/null || true)"
[[ -n "$REMAINING" ]] && kill -9 ${(f)REMAINING} >/dev/null 2>&1 || true
echo "[OK] Reader Studio stopped."
sleep 1.5
