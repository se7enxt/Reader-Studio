#!/bin/zsh
# ==============================================================================
# Reader Studio v3.3 — Portable Zero-Prerequisite Mac Launcher
# Double-click in Finder (First time: Right-Click -> Open -> Open) to
# automatically install standalone Python 3.12 + all dependencies inside `.venv`,
# download Kokoro-82M once on first launch, and open Reader Studio in your browser.
# ==============================================================================
set -u

APP_DIR="$(cd "$(dirname "$0")" && pwd)"
RUNTIME_DIR="$APP_DIR/.runtime"
VENV_DIR="$APP_DIR/.venv"
REQ_FILE="$APP_DIR/requirements.txt"
SERVER_SCRIPT="$APP_DIR/04_SCRIPTS/KOKORO_CODE_Server_v3.3_20261116_FINAL.py"
PORT="${PORT:-7880}"
TARGET_URL="http://127.0.0.1:$PORT"
export PATH="$RUNTIME_DIR/bin:$VENV_DIR/bin:/opt/homebrew/bin:/opt/homebrew/sbin:/usr/local/bin:$HOME/.local/bin:$HOME/.cargo/bin:$PATH"

# Clear macOS Gatekeeper quarantine inside this folder on first unzip if present
xattr -dr com.apple.quarantine "$APP_DIR" >/dev/null 2>&1 || true

pause_on_error() {
    local msg="${1:-Launch failed.}"
    echo ""
    echo "============================================================"
    echo " [!] $msg"
    echo "============================================================"
    if [[ -t 0 ]]; then
        read -k 1 "?Press any key to close..."
        echo ""
    fi
    exit 1
}

open_studio_browser() {
    if [[ -d "/Applications/Google Chrome.app" ]] || [[ -d "$HOME/Applications/Google Chrome.app" ]]; then
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
        " >/dev/null 2>&1 && return 0
    fi
    open "$TARGET_URL/" >/dev/null 2>&1 || true
}

echo "============================================================"
echo " Reader Studio v3.3 — Portable Neural Document & PDF Studio"
echo " URL: $TARGET_URL/  |  100% On-Device Audio & Checkpoints"
echo "============================================================"

if [[ ! -f "$SERVER_SCRIPT" ]]; then
    pause_on_error "Server file not found at $SERVER_SCRIPT"
fi
if [[ ! -f "$REQ_FILE" ]]; then
    pause_on_error "requirements.txt not found at $REQ_FILE"
fi

# Reuse already-running v3.3 instance if live on port
if lsof -Pi :$PORT -sTCP:LISTEN -t >/dev/null 2>&1 ; then
    LIVE_STATUS="$(curl -s --max-time 2 "$TARGET_URL/api/status" || true)"
    if [[ "$LIVE_STATUS" == *"3.3.0"* ]]; then
        echo "[OK] Reader Studio v3.3 is already running on $TARGET_URL/"
        open_studio_browser
        exit 0
    else
        echo "[!] Port $PORT is busy with another process."
        echo "    Wait or re-launch with: PORT=7890 \"$0\""
        pause_on_error "Port $PORT is already in use."
    fi
fi

# Step 1/3 & 2/3: Ensure self-contained Python 3.12 runtime + dependencies
REQ_HASH="$(shasum -a 256 "$REQ_FILE" | awk '{print $1}')"
STAMP_FILE="$VENV_DIR/.installed-hash"
IMPORT_CHECK="import kokoro, misaki, espeakng_loader, imageio_ffmpeg, fastapi, uvicorn, pymupdf, docx, soundfile"

NEED_BOOTSTRAP=0
if [[ ! -x "$VENV_DIR/bin/python" ]]; then
    NEED_BOOTSTRAP=1
elif [[ "$(cat "$STAMP_FILE" 2>/dev/null || true)" != "$REQ_HASH" ]]; then
    NEED_BOOTSTRAP=1
elif ! "$VENV_DIR/bin/python" -c "$IMPORT_CHECK" >/dev/null 2>&1 ; then
    NEED_BOOTSTRAP=1
fi

if [[ "$NEED_BOOTSTRAP" -eq 1 ]]; then
    echo ""
    echo "[Step 1/3] First-time setup: preparing isolated Python 3.12 environment..."
    echo "           (Happens once, ~1-2 minutes. Future launches start in ~2 seconds!)"

    UV_BIN=""
    if command -v uv >/dev/null 2>&1; then
        UV_BIN="$(command -v uv)"
    elif [[ -x "$RUNTIME_DIR/bin/uv" ]]; then
        UV_BIN="$RUNTIME_DIR/bin/uv"
    else
        echo "  -> Installing standalone 'uv' package manager into .runtime/bin..."
        mkdir -p "$RUNTIME_DIR/bin"
        if curl -LsSf https://astral.sh/uv/install.sh | env UV_UNMANAGED_INSTALL="$RUNTIME_DIR/bin" sh ; then
            [[ -x "$RUNTIME_DIR/bin/uv" ]] && UV_BIN="$RUNTIME_DIR/bin/uv"
        else
            echo "  -> Standalone 'uv' download unavailable; checking system python3..."
        fi
    fi

    if [[ -n "$UV_BIN" ]]; then
        export UV_PYTHON_INSTALL_DIR="$RUNTIME_DIR/python"
        if [[ ! -x "$VENV_DIR/bin/python" ]]; then
            "$UV_BIN" venv --python 3.12 "$VENV_DIR" || pause_on_error "Could not create standalone Python 3.12 virtual environment (internet needed on first setup)."
        fi
        echo ""
        echo "[Step 2/3] Installing Reader Studio & Kokoro-82M packages + audio engines..."
        "$UV_BIN" pip install --python "$VENV_DIR/bin/python" -r "$REQ_FILE" || pause_on_error "Dependency installation failed. Check internet connection and retry."
    else
        SYS_PY="$(command -v python3 || true)"
        [[ -z "$SYS_PY" ]] && pause_on_error "No Python found and offline download failed. Connect to internet once and re-run."
        PY_OK="$("$SYS_PY" -c "import sys; print(1 if sys.version_info >= (3, 10) else 0)" 2>/dev/null || echo 0)"
        [[ "$PY_OK" != "1" ]] && pause_on_error "Python 3.10+ is required when installing without uv."
        if [[ ! -x "$VENV_DIR/bin/python" ]]; then
            "$SYS_PY" -m venv "$VENV_DIR" || pause_on_error "Could not create .venv with system python3."
        fi
        echo ""
        echo "[Step 2/3] Installing Reader Studio packages with pip..."
        "$VENV_DIR/bin/python" -m pip install --upgrade pip || pause_on_error "pip upgrade failed."
        "$VENV_DIR/bin/pip" install -r "$REQ_FILE" || pause_on_error "pip install failed. Check internet connection and retry."
    fi

    echo "  -> Verifying installed packages..."
    "$VENV_DIR/bin/python" -c "$IMPORT_CHECK" || pause_on_error "Core runtime verification failed after installation."
    printf "%s\n" "$REQ_HASH" > "$STAMP_FILE"
    echo "[OK] All dependencies verified and ready!"
else
    echo "[Step 1/3] Environment & packages verified (.venv ready)"
fi

# Step 3/3: Launch server and open browser once /api/status answers
echo ""
echo "[Step 3/3] Starting Reader Studio v3.3 server & loading Kokoro-82M voice weights..."
echo "           (First launch downloads Kokoro-82M weights from Hugging Face automatically)"
echo "           Your browser will open at $TARGET_URL/ as soon as the server is ready."
echo "           Keep this window open while reading (use 'Stop Reader Studio.command' or Ctrl+C to quit)."
echo ""

(
    for i in {1..180}; do
        if curl -fsS "$TARGET_URL/api/status" >/dev/null 2>&1 ; then
            open_studio_browser
            break
        fi
        sleep 0.5
    done
) &

cd "$APP_DIR/04_SCRIPTS"
"$VENV_DIR/bin/python" "$SERVER_SCRIPT" --host 127.0.0.1 --port "$PORT" --preload || pause_on_error "Reader Studio server exited unexpectedly."
