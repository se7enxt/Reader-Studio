#!/bin/zsh
# ==============================================================================
# Kokoro-82M Document & PDF Reader Studio v3.3 — Launcher
# Preserves v3.2 (/v3.2), v3.1 (/v3.1), v3.0 (/v3), v2.0 (/v2) & v1.0 (/v1);
# launches v3.3 Studio at http://127.0.0.1:7880 inside the existing Google Chrome window.
# ==============================================================================
export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:/usr/local/bin:$PATH"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PORT="${PORT:-7880}"
TARGET_URL="http://127.0.0.1:$PORT"

open_in_existing_chrome_window() {
    osascript -e "
    tell application \"Google Chrome\"
        activate
        if (count of windows) > 0 then
            set foundTab to false
            set tabIdx to 0
            repeat with t in tabs of front window
                set tabIdx to tabIdx + 1
                if URL of t starts with \"$TARGET_URL\" then
                    set URL of t to \"$TARGET_URL/\"
                    set active tab index of front window to tabIdx
                    set foundTab to true
                    exit repeat
                end if
            end repeat
            if foundTab is false then
                tell front window to make new tab with properties {URL:\"$TARGET_URL/\"}
            end if
        else
            open location \"$TARGET_URL/\"
        end if
    end tell
    " >/dev/null 2>&1 || open -a "Google Chrome" "$TARGET_URL/"
}

echo "============================================================"
echo " 🎙️ Launching Kokoro-82M Document & PDF Reader Studio v3.3..."
echo " 📍 URL: $TARGET_URL  (Default Voice: Aoede · 100 Auto-Saves)"
echo "============================================================"

# If port is already bound, check whether it's already v3.3.0
if lsof -Pi :$PORT -sTCP:LISTEN -t >/dev/null 2>&1 ; then
    LIVE_VER="$(curl -s --max-time 2 "$TARGET_URL/api/status" | grep -o '"version":"[^"]*"' | head -n1)"
    if [[ "$LIVE_VER" == *"3.3.0"* ]]; then
        echo "✓ Server v3.3 is already running on $TARGET_URL"
        open_in_existing_chrome_window
        exit 0
    else
        echo "↻ Upgrading running Kokoro server on port $PORT to v3.3..."
        OLD_PIDS="$(lsof -Pi :$PORT -sTCP:LISTEN -t)"
        if [[ -n "$OLD_PIDS" ]]; then
            kill $OLD_PIDS >/dev/null 2>&1
            sleep 1
        fi
    fi
fi

# Open/reload tab inside existing Google Chrome window once server socket is ready
(
    for i in {1..40}; do
        if lsof -Pi :$PORT -sTCP:LISTEN -t >/dev/null 2>&1 ; then
            open_in_existing_chrome_window
            break
        fi
        sleep 0.5
    done
) &

cd "$SCRIPT_DIR"
exec python3 "$SCRIPT_DIR/KOKORO_CODE_Server_v3.3_20261116_FINAL.py" --host 127.0.0.1 --port "$PORT" --preload
