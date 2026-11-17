#!/usr/bin/env python3
"""
Reader Studio v3.3 — Cross-Platform Entry Point
Starts the local neural document & PDF reader studio server (powered by Kokoro-82M)
and prints the local URL without spawning unintended browsers.
"""
import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent
SERVER_SCRIPT = ROOT_DIR / "04_SCRIPTS" / "KOKORO_CODE_Server_v3.3_20261116_FINAL.py"


def main() -> int:
    parser = argparse.ArgumentParser(description="Launch Reader Studio v3.3")
    parser.add_argument("--host", default="127.0.0.1", help="Host interface (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "7880")), help="Port (default: 7880)")
    parser.add_argument("--no-preload", action="store_true", help="Defer loading the TTS model until first synthesis request")
    args = parser.parse_args()

    if not SERVER_SCRIPT.exists():
        print(f"Error: Server script not found at {SERVER_SCRIPT}", file=sys.stderr)
        return 1

    url = f"http://{args.host}:{args.port}/"
    print("============================================================")
    print(" Reader Studio v3.3 — Local Neural Document & PDF Reader")
    print(f" Open in Google Chrome / browser: {url}")
    print("============================================================")

    cmd = [sys.executable, str(SERVER_SCRIPT), "--host", args.host, "--port", str(args.port)]
    if not args.no_preload:
        cmd.append("--preload")

    return subprocess.call(cmd, cwd=str(SERVER_SCRIPT.parent))


if __name__ == "__main__":
    raise SystemExit(main())
