#!/usr/bin/env python3
"""
Kokoro-82M Document & PDF Reader Studio v3.3 — Backend Server
Model: https://huggingface.co/hexgrad/Kokoro-82M
Preserves v3.2 (/v3.2), v3.1 (/v3.1), v3.0 (/v3), v2.0 (/v2), and v1.0 (/v1) completely untouched while adding v3.3 capabilities:
- Per-Document Automatic `.JSON` Progress Saving + Configurable Rolling Backup History (Default: last 100 auto-saves per file)
- Direct-to-Library `💾 .JSON` Save (zero browser Downloads clutter) + 1-Click Resume from Any Checkpoint in Library
- 1-Click macOS Finder Folder Access (`open` / `open -R`) for Global & Per-Document Auto-Save `.JSON` Stores in Settings & Library
- Drift-Free Monotonic Word Timestamp Alignment, Instant Click-Any-Word Playback, `C`/`S` (`1`–`7`) Composer & `P` Book Preview
- Default Reader Voice: Aoede (`af_aoede`) + Deterministic 54-Voice Color Coordination Palette
"""

import argparse
import csv
import difflib
import hashlib
import html
import io
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import soundfile as sf
import torch
import uvicorn
from fastapi import Body, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from pydantic import BaseModel, Field

# Ensure Homebrew binaries (ffmpeg, espeak-ng) are on PATH
for _bin_dir in ("/opt/homebrew/bin", "/opt/homebrew/sbin", "/usr/local/bin"):
    if os.path.isdir(_bin_dir) and _bin_dir not in os.environ.get("PATH", ""):
        os.environ["PATH"] = f"{_bin_dir}:{os.environ.get('PATH', '')}"

# Suppress harmless PyTorch RNN/weight_norm warnings from upstream StyleTTS2
import warnings
warnings.filterwarnings("ignore", category=UserWarning, module="torch.nn.modules.rnn")
warnings.filterwarnings("ignore", category=FutureWarning, module="torch.nn.utils.weight_norm")

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
EXPORTS_DIR = PROJECT_ROOT / "04_AUDIO" / "exports"
CACHE_DIR = PROJECT_ROOT / "03_ASSETS" / "cache"
LIBRARY_DIR = PROJECT_ROOT / "03_ASSETS" / "library"
PROGRESS_SAVES_DIR = LIBRARY_DIR / "progress-saves"
AUTOSAVE_SETTINGS_FILE = LIBRARY_DIR / "autosave-settings.json"
DEFAULT_MAX_AUTOSAVE_BACKUPS = 100
ASSETS_DIR = PROJECT_ROOT / "03_ASSETS"

EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
CACHE_DIR.mkdir(parents=True, exist_ok=True)
LIBRARY_DIR.mkdir(parents=True, exist_ok=True)
PROGRESS_SAVES_DIR.mkdir(parents=True, exist_ok=True)
ASSETS_DIR.mkdir(parents=True, exist_ok=True)

MODEL_REPO_ID = "hexgrad/Kokoro-82M"
SAMPLE_RATE = 24000
DEFAULT_VOICE = "af_aoede"

# ==============================================================================
# VOICE COLOR-COORDINATION PALETTE & OFFICIAL KOKORO-82M v1.0 CATALOG (54 VOICES)
# ==============================================================================
SIGNATURE_VOICE_COLORS: Dict[str, Tuple[str, str]] = {
    "af_aoede": ("#7c3aed", "Royal Iris"),
    "af_heart": ("#e11d48", "Crimson Rose"),
    "af_bella": ("#d97706", "Warm Amber"),
    "af_nicole": ("#c026d3", "Orchid Plum"),
    "af_kore": ("#0891b2", "Aegean Cyan"),
    "af_sarah": ("#059669", "Emerald Sage"),
    "af_nova": ("#ea580c", "Solar Tangelo"),
    "af_alloy": ("#4f46e5", "Cobalt Indigo"),
    "af_sky": ("#0284c7", "Cerulean Sky"),
    "af_jessica": ("#db2777", "Magenta Berry"),
    "af_river": ("#0d9488", "River Verdigris"),
    "am_fenrir": ("#1d4ed8", "Deep Sapphire"),
    "am_michael": ("#15803d", "Forest Cedar"),
    "am_puck": ("#c2410c", "Copper Flame"),
    "am_echo": ("#4338ca", "Midnight Violet"),
    "am_eric": ("#334155", "Nordic Slate"),
    "am_liam": ("#0369a1", "Ocean Steel"),
    "am_onyx": ("#6d28d9", "Velvet Amethyst"),
    "am_santa": ("#b91c1c", "Hearth Carmine"),
    "am_adam": ("#92400e", "Bronze Umber"),
    "bf_emma": ("#0f766e", "Literary Teal"),
    "bf_isabella": ("#9333ea", "Regency Violet"),
    "bf_alice": ("#be185d", " Tudor Rose"),
    "bf_lily": ("#047857", "Cotswold Moss"),
    "bm_fable": ("#3730a3", "Oxford Ink"),
    "bm_george": ("#b45309", "Antique Brass"),
    "bm_lewis": ("#1e40af", "Cambridge Navy"),
    "bm_daniel": ("#0e7490", "Thames Petrol"),
}

FALLBACK_VOICE_PALETTE: List[Tuple[str, str]] = [
    ("#7c3aed", "Iris"), ("#0284c7", "Azure"), ("#059669", "Emerald"),
    ("#d97706", "Ochre"), ("#e11d48", "Ruby"), ("#0d9488", "Teal"),
    ("#9333ea", "Amethyst"), ("#ea580c", "Sienna"), ("#2563eb", "Cobalt"),
    ("#16a34a", "Jade"), ("#db2777", "Garnet"), ("#4f46e5", "Ultramarine"),
]


def _hex_to_rgba(hex_color: str, alpha: float) -> str:
    h = hex_color.lstrip("#")
    if len(h) != 6:
        return f"rgba(124, 58, 237, {alpha})"
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"rgba({r}, {g}, {b}, {alpha})"


RAW_KOKORO_VOICES: List[Dict[str, Any]] = [
    # 🇺🇸 American English — Female (11) — Aoede first as v3.0 Default Reader
    {"id": "af_aoede", "name": "Aoede", "lang_code": "a", "language": "🇺🇸 American English", "gender": "Female", "grade": "A-", "traits": "✨ Default v3 Reader · Lyrical, Warm & Articulate", "recommended": True, "default": True},
    {"id": "af_heart", "name": "Heart", "lang_code": "a", "language": "🇺🇸 American English", "gender": "Female", "grade": "A", "traits": "❤️ Warm & Expressive", "recommended": True},
    {"id": "af_bella", "name": "Bella", "lang_code": "a", "language": "🇺🇸 American English", "gender": "Female", "grade": "A-", "traits": "🔥 Rich & Dynamic Narrator", "recommended": True},
    {"id": "af_nicole", "name": "Nicole", "lang_code": "a", "language": "🇺🇸 American English", "gender": "Female", "grade": "B-", "traits": "🎧 Soft Intimate Audiobook", "recommended": True},
    {"id": "af_kore", "name": "Kore", "lang_code": "a", "language": "🇺🇸 American English", "gender": "Female", "grade": "C+", "traits": "Crisp & Articulate"},
    {"id": "af_sarah", "name": "Sarah", "lang_code": "a", "language": "🇺🇸 American English", "gender": "Female", "grade": "C+", "traits": "Conversational & Calm"},
    {"id": "af_nova", "name": "Nova", "lang_code": "a", "language": "🇺🇸 American English", "gender": "Female", "grade": "C", "traits": "Bright & Modern"},
    {"id": "af_alloy", "name": "Alloy", "lang_code": "a", "language": "🇺🇸 American English", "gender": "Female", "grade": "C", "traits": "Balanced Studio Tone"},
    {"id": "af_sky", "name": "Sky", "lang_code": "a", "language": "🇺🇸 American English", "gender": "Female", "grade": "C-", "traits": "Light & Airy"},
    {"id": "af_jessica", "name": "Jessica", "lang_code": "a", "language": "🇺🇸 American English", "gender": "Female", "grade": "D", "traits": "Upbeat & Casual"},
    {"id": "af_river", "name": "River", "lang_code": "a", "language": "🇺🇸 American English", "gender": "Female", "grade": "D", "traits": "Smooth & Mellow"},

    # 🇺🇸 American English — Male (9)
    {"id": "am_fenrir", "name": "Fenrir", "lang_code": "a", "language": "🇺🇸 American English", "gender": "Male", "grade": "C+", "traits": "Deep, Resonant & Commanding", "recommended": True},
    {"id": "am_michael", "name": "Michael", "lang_code": "a", "language": "🇺🇸 American English", "gender": "Male", "grade": "C+", "traits": "Clear Non-Fiction Narrator", "recommended": True},
    {"id": "am_puck", "name": "Puck", "lang_code": "a", "language": "🇺🇸 American English", "gender": "Male", "grade": "C+", "traits": "Energetic & Spirited", "recommended": True},
    {"id": "am_echo", "name": "Echo", "lang_code": "a", "language": "🇺🇸 American English", "gender": "Male", "grade": "D", "traits": "Smooth Baritone"},
    {"id": "am_eric", "name": "Eric", "lang_code": "a", "language": "🇺🇸 American English", "gender": "Male", "grade": "D", "traits": "Steady & Neutral"},
    {"id": "am_liam", "name": "Liam", "lang_code": "a", "language": "🇺🇸 American English", "gender": "Male", "grade": "D", "traits": "Youthful & Direct"},
    {"id": "am_onyx", "name": "Onyx", "lang_code": "a", "language": "🇺🇸 American English", "gender": "Male", "grade": "D", "traits": "Velvet Low Register"},
    {"id": "am_santa", "name": "Santa", "lang_code": "a", "language": "🇺🇸 American English", "gender": "Male", "grade": "D-", "traits": "Warm Character Voice"},
    {"id": "am_adam", "name": "Adam", "lang_code": "a", "language": "🇺🇸 American English", "gender": "Male", "grade": "F+", "traits": "Classic Documentary"},

    # 🇬🇧 British English — Female (4)
    {"id": "bf_emma", "name": "Emma", "lang_code": "b", "language": "🇬🇧 British English", "gender": "Female", "grade": "B-", "traits": "RP Literary Audiobook Narrator", "recommended": True},
    {"id": "bf_isabella", "name": "Isabella", "lang_code": "b", "language": "🇬🇧 British English", "gender": "Female", "grade": "C", "traits": "Refined & Poised"},
    {"id": "bf_alice", "name": "Alice", "lang_code": "b", "language": "🇬🇧 British English", "gender": "Female", "grade": "D", "traits": "Gentle Storytelling"},
    {"id": "bf_lily", "name": "Lily", "lang_code": "b", "language": "🇬🇧 British English", "gender": "Female", "grade": "D", "traits": "Soft British Tone"},

    # 🇬🇧 British English — Male (4)
    {"id": "bm_fable", "name": "Fable", "lang_code": "b", "language": "🇬🇧 British English", "gender": "Male", "grade": "C", "traits": "Classic British Storyteller", "recommended": True},
    {"id": "bm_george", "name": "George", "lang_code": "b", "language": "🇬🇧 British English", "gender": "Male", "grade": "C", "traits": "Authoritative Broadcast", "recommended": True},
    {"id": "bm_lewis", "name": "Lewis", "lang_code": "b", "language": "🇬🇧 British English", "gender": "Male", "grade": "D+", "traits": "Academic & Measured"},
    {"id": "bm_daniel", "name": "Daniel", "lang_code": "b", "language": "🇬🇧 British English", "gender": "Male", "grade": "D", "traits": "Crisp Presenter"},

    # 🇮🇳 Hindi (4)
    {"id": "hf_alpha", "name": "Alpha (Hindi F)", "lang_code": "h", "language": "🇮🇳 Hindi", "gender": "Female", "grade": "C", "traits": "Clear Hindi Female"},
    {"id": "hf_beta", "name": "Beta (Hindi F)", "lang_code": "h", "language": "🇮🇳 Hindi", "gender": "Female", "grade": "C", "traits": "Soft Hindi Female"},
    {"id": "hm_omega", "name": "Omega (Hindi M)", "lang_code": "h", "language": "🇮🇳 Hindi", "gender": "Male", "grade": "C", "traits": "Deep Hindi Male"},
    {"id": "hm_psi", "name": "Psi (Hindi M)", "lang_code": "h", "language": "🇮🇳 Hindi", "gender": "Male", "grade": "C", "traits": "Natural Hindi Male"},

    # 🇪🇸 Spanish (3)
    {"id": "ef_dora", "name": "Dora", "lang_code": "e", "language": "🇪🇸 Spanish", "gender": "Female", "grade": "C", "traits": "Spanish Female"},
    {"id": "em_alex", "name": "Alex", "lang_code": "e", "language": "🇪🇸 Spanish", "gender": "Male", "grade": "C", "traits": "Spanish Male"},
    {"id": "em_santa", "name": "Santa (ES)", "lang_code": "e", "language": "🇪🇸 Spanish", "gender": "Male", "grade": "C-", "traits": "Warm Spanish Male"},

    # 🇫🇷 French (1)
    {"id": "ff_siwis", "name": "Siwis", "lang_code": "f", "language": "🇫🇷 French", "gender": "Female", "grade": "B-", "traits": "Studio French Female"},

    # 🇮🇹 Italian (2)
    {"id": "if_sara", "name": "Sara", "lang_code": "i", "language": "🇮🇹 Italian", "gender": "Female", "grade": "C", "traits": "Italian Female"},
    {"id": "im_nicola", "name": "Nicola", "lang_code": "i", "language": "🇮🇹 Italian", "gender": "Male", "grade": "C", "traits": "Italian Male"},

    # 🇧🇷 Brazilian Portuguese (3)
    {"id": "pf_dora", "name": "Dora (PT-BR)", "lang_code": "p", "language": "🇧🇷 Portuguese", "gender": "Female", "grade": "C", "traits": "Brazilian Portuguese Female"},
    {"id": "pm_alex", "name": "Alex (PT-BR)", "lang_code": "p", "language": "🇧🇷 Portuguese", "gender": "Male", "grade": "C", "traits": "Brazilian Portuguese Male"},
    {"id": "pm_santa", "name": "Santa (PT-BR)", "lang_code": "p", "language": "🇧🇷 Portuguese", "gender": "Male", "grade": "C-", "traits": "Warm Portuguese Male"},

    # 🇯🇵 Japanese (5)
    {"id": "jf_alpha", "name": "Alpha (JP)", "lang_code": "j", "language": "🇯🇵 Japanese", "gender": "Female", "grade": "C+", "traits": "Natural Japanese Female"},
    {"id": "jf_gongitsune", "name": "Gongitsune", "lang_code": "j", "language": "🇯🇵 Japanese", "gender": "Female", "grade": "C", "traits": "Storybook Narrator"},
    {"id": "jf_tebukuro", "name": "Tebukuro", "lang_code": "j", "language": "🇯🇵 Japanese", "gender": "Female", "grade": "C", "traits": "Gentle Japanese Female"},
    {"id": "jf_nezumi", "name": "Nezumi", "lang_code": "j", "language": "🇯🇵 Japanese", "gender": "Female", "grade": "C-", "traits": "Light Japanese Female"},
    {"id": "jm_kumo", "name": "Kumo", "lang_code": "j", "language": "🇯🇵 Japanese", "gender": "Male", "grade": "C-", "traits": "Japanese Male Narrator"},

    # 🇨🇳 Mandarin Chinese (8)
    {"id": "zf_xiaobei", "name": "Xiaobei", "lang_code": "z", "language": "🇨🇳 Mandarin Chinese", "gender": "Female", "grade": "D", "traits": "Mandarin Female"},
    {"id": "zf_xiaoni", "name": "Xiaoni", "lang_code": "z", "language": "🇨🇳 Mandarin Chinese", "gender": "Female", "grade": "D", "traits": "Mandarin Female"},
    {"id": "zf_xiaoxiao", "name": "Xiaoxiao", "lang_code": "z", "language": "🇨🇳 Mandarin Chinese", "gender": "Female", "grade": "D", "traits": "Mandarin Female"},
    {"id": "zf_xiaoyi", "name": "Xiaoyi", "lang_code": "z", "language": "🇨🇳 Mandarin Chinese", "gender": "Female", "grade": "D", "traits": "Mandarin Female"},
    {"id": "zm_yunjian", "name": "Yunjian", "lang_code": "z", "language": "🇨🇳 Mandarin Chinese", "gender": "Male", "grade": "D", "traits": "Mandarin Male"},
    {"id": "zm_yunxi", "name": "Yunxi", "lang_code": "z", "language": "🇨🇳 Mandarin Chinese", "gender": "Male", "grade": "D", "traits": "Mandarin Male"},
    {"id": "zm_yunxia", "name": "Yunxia", "lang_code": "z", "language": "🇨🇳 Mandarin Chinese", "gender": "Male", "grade": "D", "traits": "Mandarin Male"},
    {"id": "zm_yunyang", "name": "Yunyang", "lang_code": "z", "language": "🇨🇳 Mandarin Chinese", "gender": "Male", "grade": "D", "traits": "Mandarin Male"},
]

KOKORO_VOICES: List[Dict[str, Any]] = []
for _idx, _v in enumerate(RAW_KOKORO_VOICES):
    _vid = _v["id"]
    if _vid in SIGNATURE_VOICE_COLORS:
        _hex, _cname = SIGNATURE_VOICE_COLORS[_vid]
    else:
        _hex, _cname = FALLBACK_VOICE_PALETTE[_idx % len(FALLBACK_VOICE_PALETTE)]
    KOKORO_VOICES.append({
        **_v,
        "language_label": _v["language"],
        "color": _hex,
        "color_name": _cname.strip(),
        "bg_tint": _hex_to_rgba(_hex, 0.14),
        "border_tint": _hex_to_rgba(_hex, 0.42),
    })

VOICE_MAP: Dict[str, Dict[str, Any]] = {v["id"]: v for v in KOKORO_VOICES}


def get_voice_color_meta(voice_id: Optional[str]) -> Dict[str, str]:
    vid = (voice_id or DEFAULT_VOICE).strip()
    info = VOICE_MAP.get(vid) or VOICE_MAP.get(DEFAULT_VOICE) or {}
    return {
        "voice_id": info.get("id", vid),
        "voice_name": info.get("name", vid),
        "color": info.get("color", "#7c3aed"),
        "color_name": info.get("color_name", "Royal Iris"),
        "bg_tint": info.get("bg_tint", "rgba(124, 58, 237, 0.14)"),
        "border_tint": info.get("border_tint", "rgba(124, 58, 237, 0.42)"),
    }


# ==============================================================================
# MARKDOWN & FORMATTING CHARACTER SANITIZER (DEFAULT ON, TOGGLEABLE)
# ==============================================================================
def strip_markdown_formatting(text: str, enabled: bool = True) -> str:
    """
    When enabled=True (default), omits Markdown and formatting characters (asterisks **, *,
    heading hashes #, underscores __, strikethrough ~~, inline backticks `, blockquotes >,
    horizontal rules ---, and Markdown link URLs) so Kokoro never reads 'asterisk' or raw
    formatting aloud, while preserving Kokoro phonetic overrides `[word](/ipa/)`.
    """
    if not text or not enabled:
        return text or ""

    ipa_placeholders: List[str] = []

    def _save_ipa(match: re.Match) -> str:
        idx = len(ipa_placeholders)
        ipa_placeholders.append(match.group(0))
        return f"@@KOKOROIPA{idx}@@"

    out = re.sub(r"\[[^\]]+\]\(/[^\)]+/\)", _save_ipa, text)
    out = re.sub(r"(?m)^\s*`{3,}[a-zA-Z0-9_-]*\s*$", "", out)
    out = re.sub(r"`{3,}", "", out)
    out = re.sub(r"!\[([^\]]*)\]\([^\)]+\)", r"\1", out)
    out = re.sub(r"\[([^\]]+)\]\([^\)]+\)", r"\1", out)
    out = re.sub(r"\[([^\]]+)\]\[[^\]]*\]", r"\1", out)
    out = re.sub(r"(?m)^\s*([-*_]\s*){3,}\s*$", "", out)
    out = re.sub(r"(?m)^\s*#{1,6}\s+", "", out)
    out = re.sub(r"(?m)\s+#{1,6}\s*$", "", out)
    out = re.sub(r"(?m)^\s*>+\s*", "", out)
    out = re.sub(r"(?<=\s)>+(?=\s+[—–\-A-Za-z0-9\"'“‘(])", "", out)
    out = re.sub(r"(?m)^\s*[-*+]\s+\[[ xX]\]\s+", "", out)
    out = re.sub(r"(?m)^\s*[\*\+]\s+", "", out)
    out = re.sub(r"\*{1,3}([^*]+)\*{1,3}", r"\1", out)
    out = re.sub(r"(?<!\w)_{1,3}([^_]+)_{1,3}(?!\w)", r"\1", out)
    out = re.sub(r"~~([^~]+)~~", r"\1", out)
    out = re.sub(r"`+([^`]+)`+", r"\1", out)
    out = re.sub(r"\*+", "", out)
    out = re.sub(r"`+", "", out)
    out = re.sub(r"~{2,}", "", out)
    out = re.sub(r"(?<!\S)#{1,6}(?!\S)", "", out)
    out = re.sub(r"_{2,}", " ", out)

    for idx, original in enumerate(ipa_placeholders):
        out = out.replace(f"@@KOKOROIPA{idx}@@", original)

    out = re.sub(r"[ \t]+", " ", out).strip()
    return out


HORIZ_RULE_ONLY_RE = re.compile(r"^\s*(?:[-*_—–=~•✦❖]\s*){3,}\s*$|^\s*[-*_—–=~•✦❖]+\s*$")


def is_decorative_divider_only(text: str) -> bool:
    if not text or not text.strip():
        return True
    return bool(HORIZ_RULE_ONLY_RE.match(text.strip()))


def has_speakable_content(text: str, strip_md: bool = True) -> bool:
    """Returns True only if `text` contains at least one pronounceable letter or digit."""
    if not text or is_decorative_divider_only(text):
        return False
    cleaned = strip_markdown_formatting(text, enabled=strip_md).strip()
    if not cleaned or is_decorative_divider_only(cleaned):
        return False
    # Check for at least one Unicode alphanumeric character (not underscore/punctuation)
    return bool(re.search(r"[^\W_]", cleaned, re.UNICODE))


# ==============================================================================
# REAL-TIME TASK PROGRESS, QUEUE & CANCELLATION REGISTRY
# ==============================================================================
TASKS_STORE: Dict[str, Dict[str, Any]] = {}
CANCELLED_TASKS: set[str] = set()
TASKS_LOCK = threading.Lock()


class TaskCancelledError(Exception):
    """Raised when a user cancels an active or queued task."""
    pass


def is_task_cancelled(task_id: Optional[str]) -> bool:
    if not task_id:
        return False
    with TASKS_LOCK:
        return task_id in CANCELLED_TASKS


def check_task_cancelled(task_id: Optional[str]) -> None:
    if not task_id:
        return
    if is_task_cancelled(task_id):
        update_task_progress(task_id, 100.0, "Task Cancelled", "Cancelled by user.", status="cancelled")
        raise TaskCancelledError("Task cancelled by user.")


def cancel_task_by_id(task_id: str) -> Dict[str, Any]:
    with TASKS_LOCK:
        CANCELLED_TASKS.add(task_id)
        entry = TASKS_STORE.get(task_id)
        if entry:
            entry["status"] = "cancelled"
            entry["stage"] = "Cancelled by user"
            entry["detail"] = "Task execution stopped."
            entry["updated_at"] = time.time()
            return dict(entry)
    return {"task_id": task_id, "status": "cancelled", "stage": "Cancelled by user"}


def cancel_all_running_tasks() -> int:
    count = 0
    with TASKS_LOCK:
        for tid, entry in TASKS_STORE.items():
            if entry.get("status") in ("running", "queued"):
                CANCELLED_TASKS.add(tid)
                entry["status"] = "cancelled"
                entry["stage"] = "Cancelled by user"
                entry["detail"] = "All tasks cancelled."
                entry["updated_at"] = time.time()
                count += 1
    return count


def update_task_progress(
    task_id: Optional[str],
    percent: float,
    stage: str,
    detail: str = "",
    current_step: int = 0,
    total_steps: int = 0,
    status: str = "running",
) -> None:
    if not task_id:
        return
    now = time.time()
    pct = round(max(0.0, min(100.0, float(percent))), 1)
    with TASKS_LOCK:
        if task_id in CANCELLED_TASKS and status != "cancelled":
            status = "cancelled"
        existing = TASKS_STORE.get(task_id)
        started_at = existing["started_at"] if existing else now
        elapsed = round(max(0.01, now - started_at), 2)
        eta_sec: Optional[float] = None
        if status == "running" and 4.0 <= pct < 99.5:
            eta_sec = round(max(0.1, elapsed * ((100.0 - pct) / pct)), 1)
        elif status in ("completed", "cancelled"):
            eta_sec = 0.0
        TASKS_STORE[task_id] = {
            "task_id": task_id,
            "status": status,
            "percent": pct,
            "stage": stage,
            "detail": detail,
            "current_step": int(current_step),
            "total_steps": int(total_steps),
            "started_at": started_at,
            "updated_at": now,
            "elapsed_sec": elapsed,
            "eta_sec": eta_sec,
        }


# ==============================================================================
# GENERATED AUDIO MANIFEST REGISTRY (EPISODE / PAGE / PARAGRAPH / VOICE TRACKING)
# ==============================================================================
GENERATED_MANIFEST_FILE = LIBRARY_DIR / "generated-manifest.json"
MANIFEST_LOCK = threading.Lock()


def _load_generated_manifest_unlocked() -> Dict[str, Any]:
    if not GENERATED_MANIFEST_FILE.exists():
        return {"docs": {}}
    try:
        data = json.loads(GENERATED_MANIFEST_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("docs"), dict):
            return data
    except Exception:
        pass
    return {"docs": {}}


def _save_generated_manifest_unlocked(data: Dict[str, Any]) -> None:
    try:
        GENERATED_MANIFEST_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception as exc:
        print(f"[Manifest] Warning saving generated manifest: {exc}")


def record_generated_audio_item(
    doc_id: Optional[str],
    meta: Dict[str, Any],
    scope: str = "unit",
    page_number: Optional[int] = None,
    chapter_number: Optional[int] = None,
    unit_index: Optional[int] = None,
    chunk_mode: str = "paragraph",
) -> None:
    if not doc_id or not meta.get("job_id"):
        return
    v_meta = get_voice_color_meta(meta.get("voice", DEFAULT_VOICE))
    entry = {
        "job_id": meta["job_id"],
        "scope": scope,
        "page_number": page_number,
        "chapter_number": chapter_number,
        "unit_index": unit_index,
        "chunk_mode": chunk_mode,
        "voice": v_meta["voice_id"],
        "voice_name": v_meta["voice_name"],
        "voice_color": v_meta["color"],
        "blend_voice": meta.get("blend_voice"),
        "speed": meta.get("speed", 1.0),
        "duration_sec": meta.get("duration_sec", 0.0),
        "audio_url": meta.get("audio_url", f"/api/audio/{meta['job_id']}/stream.wav"),
        "wav_url": meta.get("audio_url", f"/api/audio/{meta['job_id']}/stream.wav"),
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "updated_ts": int(time.time()),
    }
    with MANIFEST_LOCK:
        manifest = _load_generated_manifest_unlocked()
        doc_bucket = manifest["docs"].setdefault(doc_id, {"items": {}})
        if scope == "unit" and page_number is not None and unit_index is not None:
            key = f"{chunk_mode}:p{page_number}:u{unit_index}"
        elif scope == "chapter" and chapter_number is not None:
            key = f"chapter:c{chapter_number}"
        elif scope == "page" and page_number is not None:
            key = f"{chunk_mode}:page{page_number}"
        else:
            key = f"custom:{meta['job_id']}"
        doc_bucket["items"][key] = entry
        _save_generated_manifest_unlocked(manifest)


def prune_missing_generated_manifest_entries() -> int:
    removed = 0
    with MANIFEST_LOCK:
        manifest = _load_generated_manifest_unlocked()
        changed = False
        for doc_id, bucket in list(manifest.get("docs", {}).items()):
            items = bucket.get("items", {})
            for k, item in list(items.items()):
                jid = re.sub(r"[^a-f0-9]", "", str(item.get("job_id", "")).lower())
                wav_p = EXPORTS_DIR / f"kokoro_{jid}.wav"
                if not jid or not wav_p.exists():
                    del items[k]
                    removed += 1
                    changed = True
            if not items:
                del manifest["docs"][doc_id]
                changed = True
        if changed:
            _save_generated_manifest_unlocked(manifest)
    return removed


def get_generated_status_for_doc(doc_id: str, doc_obj: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    prune_missing_generated_manifest_entries()
    with MANIFEST_LOCK:
        manifest = _load_generated_manifest_unlocked()
        raw_items = dict(manifest.get("docs", {}).get(doc_id, {}).get("items", {}))

    units_out: Dict[str, Dict[str, Any]] = {}
    pages_full_jobs: Dict[int, Dict[str, Any]] = {}
    chapters_full_jobs: Dict[int, Dict[str, Any]] = {}
    voice_counts: Dict[str, Dict[str, Any]] = {}

    for key, item in raw_items.items():
        vid = item.get("voice", DEFAULT_VOICE)
        vc = voice_counts.setdefault(vid, {
            **get_voice_color_meta(vid),
            "generated_count": 0,
            "total_duration_sec": 0.0,
        })
        vc["generated_count"] += 1
        vc["total_duration_sec"] = round(vc["total_duration_sec"] + float(item.get("duration_sec", 0.0)), 2)

        sc = item.get("scope")
        if sc == "unit":
            units_out[key] = item
        elif sc == "page" and item.get("page_number") is not None:
            pages_full_jobs[int(item["page_number"])] = item
        elif sc == "chapter" and item.get("chapter_number") is not None:
            chapters_full_jobs[int(item["chapter_number"])] = item

    pages_summary: List[Dict[str, Any]] = []
    chapters_summary: List[Dict[str, Any]] = []

    if doc_obj and "pages" in doc_obj:
        for p in doc_obj["pages"]:
            p_num = p["page_number"]
            total_paras = len(p.get("paragraphs") or [])
            total_lines = len(p.get("lines") or [])
            para_voices = set()
            gen_paras = 0
            for u_idx in range(total_paras):
                uk = f"paragraph:p{p_num}:u{u_idx}"
                if uk in units_out:
                    gen_paras += 1
                    para_voices.add(units_out[uk]["voice"])
            full_page_job = pages_full_jobs.get(p_num)
            if full_page_job:
                gen_paras = total_paras
                para_voices.add(full_page_job["voice"])

            if total_paras == 0:
                status = "empty"
            elif gen_paras >= total_paras:
                status = "full"
            elif gen_paras > 0:
                status = "partial"
            else:
                status = "none"

            pages_summary.append({
                "page_number": p_num,
                "generated_paragraphs": gen_paras,
                "total_paragraphs": total_paras,
                "total_lines": total_lines,
                "status": status,
                "page_job": full_page_job,
                "voices": [get_voice_color_meta(v) for v in sorted(para_voices)],
            })

        for ch in doc_obj.get("chapters") or []:
            c_num = ch["chapter_number"]
            refs = ch.get("paragraph_refs") or []
            total_c_paras = len(refs)
            gen_c_paras = 0
            ch_voices = set()
            for ref in refs:
                rp = ref["page_number"]
                ru = ref["paragraph_index"]
                uk = f"paragraph:p{rp}:u{ru}"
                if uk in units_out:
                    gen_c_paras += 1
                    ch_voices.add(units_out[uk]["voice"])
                elif rp in pages_full_jobs:
                    gen_c_paras += 1
                    ch_voices.add(pages_full_jobs[rp]["voice"])
            full_ch_job = chapters_full_jobs.get(c_num)
            if full_ch_job:
                gen_c_paras = total_c_paras
                ch_voices.add(full_ch_job["voice"])

            if total_c_paras == 0:
                c_status = "empty"
            elif gen_c_paras >= total_c_paras:
                c_status = "full"
            elif gen_c_paras > 0:
                c_status = "partial"
            else:
                c_status = "none"

            chapters_summary.append({
                "chapter_number": c_num,
                "title": ch.get("title", f"Chapter {c_num}"),
                "generated_paragraphs": gen_c_paras,
                "total_paragraphs": total_c_paras,
                "status": c_status,
                "chapter_job": full_ch_job,
                "voices": [get_voice_color_meta(v) for v in sorted(ch_voices)],
            })

    return {
        "doc_id": doc_id,
        "units": units_out,
        "page_jobs": pages_full_jobs,
        "chapter_jobs": chapters_full_jobs,
        "pages_summary": pages_summary,
        "chapters_summary": chapters_summary,
        "voices_used": sorted(voice_counts.values(), key=lambda x: x["generated_count"], reverse=True),
        "total_generated_items": len(raw_items),
    }


# ==============================================================================
# KOKORO-82M ENGINE SINGLETON (PARAGRAPH-FIRST FAST START, LINE & CHAPTER MODES)
# ==============================================================================
SERVER_BOOT_TS = time.time()


def sanitize_segment_word_timings(
    words: List[Dict[str, Any]],
    seg_start: float = 0.0,
    seg_end: Optional[float] = None,
) -> List[Dict[str, Any]]:
    """
    Heals unphonemized symbol/punctuation tokens (where Kokoro returned start_ts=None/end_ts=None,
    which previously produced bogus [0.0, full_duration] spans like ('>', 0.0, 9.525)) and ensures
    strictly monotonic, tight timestamps for both newly synthesized and legacy cached segments.
    """
    if not words:
        return []
    cleaned: List[Dict[str, Any]] = []
    n = len(words)

    def _is_speakable_word(txt: str) -> bool:
        return bool(re.search(r"[^\W_]", txt or "", re.UNICODE))

    # Identify genuinely timed entries vs bogus full-segment spans
    seg_span = (float(seg_end) - float(seg_start)) if seg_end is not None else None
    valid_flags: List[bool] = []
    for w in words:
        wt = str(w.get("word", ""))
        st = w.get("start")
        en = w.get("end")
        if st is None or en is None:
            valid_flags.append(False)
            continue
        st_f = float(st)
        en_f = float(en)
        dur = en_f - st_f
        is_spk = _is_speakable_word(wt)
        # Legacy cached unphonemized punctuation/symbols had start == seg_start and end >= seg_end - 0.15
        if not is_spk and (dur > 0.65 or (seg_span and seg_span > 1.0 and dur >= seg_span * 0.75)):
            valid_flags.append(False)
        elif dur < -0.001:
            valid_flags.append(False)
        else:
            valid_flags.append(True)

    running_ts = float(seg_start or 0.0)
    for idx, w in enumerate(words):
        wt = str(w.get("word", ""))
        ws = w.get("whitespace", " ")
        is_spk = _is_speakable_word(wt)
        if valid_flags[idx]:
            st_f = max(running_ts if is_spk and float(w["start"]) < running_ts - 0.25 else 0.0, float(w["start"]))
            en_f = max(st_f, float(w["end"]))
            running_ts = max(running_ts, en_f)
            cleaned.append({
                "word": wt,
                "whitespace": ws,
                "start": round(st_f, 3),
                "end": round(en_f, 3),
                "speakable": is_spk,
            })
        else:
            # Find next valid start timestamp to interpolate if this token is actually a speakable word
            next_valid_st = None
            gap_spk_count = 1
            for j in range(idx + 1, n):
                if valid_flags[j]:
                    next_valid_st = float(words[j]["start"])
                    break
                if _is_speakable_word(str(words[j].get("word", ""))):
                    gap_spk_count += 1
            if is_spk:
                target_end = next_valid_st if (next_valid_st is not None and next_valid_st > running_ts) else min(float(seg_end or (running_ts + 0.18)), running_ts + 0.22 * gap_spk_count)
                step = max(0.04, (target_end - running_ts) / max(1, gap_spk_count))
                st_f = running_ts
                en_f = running_ts + step
                running_ts = en_f
                cleaned.append({
                    "word": wt,
                    "whitespace": ws,
                    "start": round(st_f, 3),
                    "end": round(en_f, 3),
                    "speakable": True,
                })
            else:
                # Non-speakable punctuation/symbol with no phoneme duration: zero-width anchor at running_ts
                cleaned.append({
                    "word": wt,
                    "whitespace": ws,
                    "start": round(running_ts, 3),
                    "end": round(running_ts, 3),
                    "speakable": False,
                })
    return cleaned


class KokoroEngine:
    def __init__(self):
        self._lock = threading.Lock()
        self._synth_lock = threading.Lock()
        self._model = None
        self._pipelines: Dict[str, Any] = {}
        self._device = "cpu"
        self._status = "idle"
        self._error: Optional[str] = None
        self._loaded_at: Optional[float] = None
        self._active_syntheses: int = 0
        self._total_syntheses: int = 0
        self._last_synthesis_ts: Optional[float] = None
        self._last_rtf: Optional[float] = None
        self._last_duration_sec: Optional[float] = None

    def ensure_model(self):
        if self._model is not None:
            return self._model
        with self._lock:
            if self._model is not None:
                return self._model
            self._status = "loading_model"
            try:
                from kokoro import KModel
                self._model = KModel(repo_id=MODEL_REPO_ID).to(self._device).eval()
                self._status = "ready"
                self._error = None
                self._loaded_at = time.time()
            except Exception as e:
                self._status = "error"
                self._error = str(e)
                raise
        return self._model

    def get_pipeline(self, lang_code: str):
        lang_code = (lang_code or "a").lower()[0]
        if lang_code in self._pipelines:
            return self._pipelines[lang_code]
        model = self.ensure_model()
        with self._lock:
            if lang_code in self._pipelines:
                return self._pipelines[lang_code]
            from kokoro import KPipeline
            pipe = KPipeline(lang_code=lang_code, repo_id=MODEL_REPO_ID, model=model, device=self._device)
            self._pipelines[lang_code] = pipe
            return pipe

    def resolve_voice_pack(self, pipe: Any, voice: str, blend_voice: Optional[str] = None, blend_weight: float = 0.0):
        voice = (voice or DEFAULT_VOICE).strip()
        if blend_voice and blend_voice.strip() and blend_voice != voice and 0.0 < blend_weight < 1.0:
            v1 = pipe.load_voice(voice)
            second_lang = blend_voice[0].lower()
            pipe2 = self.get_pipeline(second_lang)
            v2 = pipe2.load_voice(blend_voice.strip())
            w = float(max(0.0, min(1.0, blend_weight)))
            return (1.0 - w) * v1 + w * v2
        return voice

    def synthesize(
        self,
        text: str,
        voice: str = DEFAULT_VOICE,
        speed: float = 1.0,
        blend_voice: Optional[str] = None,
        blend_weight: float = 0.0,
        chunk_mode: str = "paragraph",
        pause_between_segments_sec: Optional[float] = None,
        paragraphs: Optional[List[str]] = None,
        lines: Optional[List[str]] = None,
        strip_markdown: bool = True,
        task_id: Optional[str] = None,
        title_hint: Optional[str] = None,
    ) -> Dict[str, Any]:
        raw_mode = (chunk_mode or "paragraph").lower().strip()
        if raw_mode not in ("paragraph", "line", "chapter"):
            raw_mode = "paragraph"
        chunk_mode = raw_mode

        update_task_progress(
            task_id,
            5.0,
            f"Preparing text for Kokoro-82M ({chunk_mode.title()} mode)…",
            "Sanitizing Markdown formatting & segmenting reading units",
            0,
            1,
        )

        parsed_paragraphs, parsed_lines = extract_text_paragraphs_and_lines(text)
        base_paragraphs = [p.strip() for p in (paragraphs or []) if p and p.strip()] or parsed_paragraphs
        base_lines = [l.strip() for l in (lines or []) if l and l.strip()] or parsed_lines

        raw_units = base_lines if chunk_mode == "line" else base_paragraphs
        units: List[str] = []
        for u in raw_units:
            if is_decorative_divider_only(u):
                continue
            cleaned_u = strip_markdown_formatting(u, enabled=strip_markdown).strip()
            if cleaned_u and has_speakable_content(cleaned_u, strip_md=False):
                units.append(cleaned_u)

        speed = float(max(0.4, min(2.5, speed)))
        voice = voice if voice in VOICE_MAP else DEFAULT_VOICE
        lang_code = VOICE_MAP.get(voice, {}).get("lang_code", voice[0].lower())
        voice_info = VOICE_MAP.get(voice, {})
        voice_name = voice_info.get("name", voice)
        voice_color_meta = get_voice_color_meta(voice)

        def _write_silent_skip_response(reason_note: str) -> Dict[str, Any]:
            silent_key = json.dumps({"silent_skip": True, "voice": voice, "raw": (text or "")[:120]}, sort_keys=True)
            silent_job_id = hashlib.sha256(silent_key.encode("utf-8")).hexdigest()[:20]
            silent_wav = EXPORTS_DIR / f"kokoro_{silent_job_id}.wav"
            silent_meta_path = EXPORTS_DIR / f"kokoro_{silent_job_id}.json"
            silent_samples = np.zeros(int(SAMPLE_RATE * 0.06), dtype=np.float32)
            if not silent_wav.exists():
                sf.write(str(silent_wav), silent_samples, SAMPLE_RATE, subtype="PCM_16")
            s_url = f"/api/audio/{silent_job_id}/stream.wav"
            s_meta = {
                "job_id": silent_job_id,
                "model": MODEL_REPO_ID,
                "chunk_mode": chunk_mode,
                "strip_markdown": bool(strip_markdown),
                "voice": voice,
                "voice_name": voice_name,
                "voice_color": voice_color_meta["color"],
                "voice_info": voice_info,
                "blend_voice": None,
                "blend_weight": 0.0,
                "speed": speed,
                "sample_rate": SAMPLE_RATE,
                "duration_sec": 0.06,
                "duration_seconds": 0.06,
                "synthesis_time_sec": 0.0,
                "rtf": 0.0,
                "char_count": 0,
                "word_count": 0,
                "segments": [],
                "skipped_silent": True,
                "skip_reason": reason_note,
                "audio_url": s_url,
                "wav_url": s_url,
                "audio_wav_url": s_url,
                "downloads": {
                    "full_mp3": f"/api/audio/{silent_job_id}/download?quality=full_mp3",
                    "low_mp3": f"/api/audio/{silent_job_id}/download?quality=low_mp3",
                    "full_wav": f"/api/audio/{silent_job_id}/download?quality=full_wav",
                },
                "cached": True,
            }
            silent_meta_path.write_text(json.dumps(s_meta, indent=2, ensure_ascii=False), encoding="utf-8")
            update_task_progress(task_id, 100.0, "Skipped formatting divider", reason_note, 1, 1, status="completed")
            return s_meta

        if not units:
            return _write_silent_skip_response("Non-spoken divider or empty formatting line skipped cleanly.")

        total_units = len(units)
        unit_label = "Line" if chunk_mode == "line" else "Paragraph"

        if pause_between_segments_sec is None:
            pause_between_segments_sec = 0.12 if chunk_mode == "line" else 0.26

        cache_key_raw = json.dumps({
            "model": MODEL_REPO_ID,
            "units": units,
            "chunk_mode": chunk_mode,
            "strip_markdown": bool(strip_markdown),
            "voice": voice,
            "speed": round(speed, 3),
            "blend_voice": blend_voice or "",
            "blend_weight": round(float(blend_weight or 0.0), 3),
            "pause": round(pause_between_segments_sec, 3),
        }, sort_keys=True, ensure_ascii=False)
        job_id = hashlib.sha256(cache_key_raw.encode("utf-8")).hexdigest()[:20]

        wav_path = EXPORTS_DIR / f"kokoro_{job_id}.wav"
        meta_path = EXPORTS_DIR / f"kokoro_{job_id}.json"

        if wav_path.exists() and meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                stream_url = meta.get("audio_url") or f"/api/audio/{job_id}/stream.wav"
                meta["audio_url"] = stream_url
                meta["wav_url"] = stream_url
                meta["audio_wav_url"] = stream_url
                meta["cached"] = True
                meta["voice_info"] = voice_info
                meta["voice_color"] = voice_color_meta["color"]
                for s in meta.get("segments") or []:
                    s.setdefault("voice", voice)
                    s.setdefault("voice_name", voice_name)
                    s.setdefault("voice_color", voice_color_meta["color"])
                    if s.get("words"):
                        s["words"] = sanitize_segment_word_timings(
                            s["words"],
                            seg_start=float(s.get("start", 0.0)),
                            seg_end=float(s.get("end", meta.get("duration_sec", 0.0))),
                        )
                if "duration_seconds" not in meta and "duration_sec" in meta:
                    meta["duration_seconds"] = meta["duration_sec"]
                update_task_progress(
                    task_id,
                    100.0,
                    f"Loaded cached Kokoro-82M audio ({meta.get('duration_sec', 0)}s)",
                    f"Instant playback ready ({total_units} {unit_label.lower()}{'s' if total_units != 1 else ''})",
                    total_units,
                    total_units,
                    status="completed",
                )
                return meta
            except Exception:
                pass

        t0 = time.time()
        check_task_cancelled(task_id)
        update_task_progress(
            task_id,
            12.0,
            f"Loading Kokoro-82M pipeline — Voice: {voice_name} ({speed:.2f}x)…",
            f"Preparing {total_units} {unit_label.lower()}(s) for neural speech synthesis",
            0,
            total_units,
        )

        # Serialize neural forward passes cleanly so background paragraph prefetching
        # never collides with an active foreground click on PyTorch CPU tensors.
        self._active_syntheses += 1
        try:
            with self._synth_lock:
                check_task_cancelled(task_id)
                pipe = self.get_pipeline(lang_code)
                voice_spec = self.resolve_voice_pack(pipe, voice, blend_voice, blend_weight)
                check_task_cancelled(task_id)

                first_preview = (units[0][:68] + "…") if len(units[0]) > 68 else units[0]
                update_task_progress(
                    task_id,
                    16.0,
                    f"Synthesizing {unit_label} 1 of {total_units} ({voice_name} · {speed:.2f}x)…",
                    f"Speaking: “{first_preview}”",
                    1,
                    total_units,
                )

                generator = pipe(units, voice=voice_spec, speed=speed, split_pattern=None)

                grouped_results: Dict[int, List[Any]] = {}
                fallback_idx = 0
                for result in generator:
                    check_task_cancelled(task_id)
                    t_idx = result.text_index if getattr(result, "text_index", None) is not None else fallback_idx
                    grouped_results.setdefault(t_idx, []).append(result)
                    completed_count = len(grouped_results)
                    pct = 16.0 + (completed_count / max(1, total_units)) * 72.0
                    next_idx = min(total_units - 1, completed_count)
                    snippet = units[next_idx][:68] + ("…" if len(units[next_idx]) > 68 else "")
                    update_task_progress(
                        task_id,
                        pct,
                        f"Synthesized {unit_label} {completed_count} of {total_units} ({voice_name} · {speed:.2f}x)",
                        f"Processing: “{snippet}”",
                        completed_count,
                        total_units,
                    )
                    fallback_idx += 1
        finally:
            self._active_syntheses = max(0, self._active_syntheses - 1)

        update_task_progress(
            task_id,
            91.0,
            "Stitching 24kHz audio waveform & aligning word-level timestamps…",
            f"Combining {len(grouped_results)} synthesized {unit_label.lower()}(s) with natural breath pauses",
            total_units,
            total_units,
        )

        audio_chunks: List[np.ndarray] = []
        segments_meta: List[Dict[str, Any]] = []
        inter_unit_pause_samples = int(SAMPLE_RATE * max(0.0, pause_between_segments_sec))
        inter_unit_pause_array = np.zeros(inter_unit_pause_samples, dtype=np.float32)
        intra_para_pause_samples = int(SAMPLE_RATE * 0.05)
        intra_para_pause_array = np.zeros(intra_para_pause_samples, dtype=np.float32)

        cursor_samples = 0
        seg_index = 0

        for unit_idx in sorted(grouped_results.keys()):
            sub_results = grouped_results[unit_idx]
            unit_audio_parts: List[np.ndarray] = []
            unit_gs_parts: List[str] = []
            unit_ps_parts: List[str] = []
            unit_words_meta: List[Dict[str, Any]] = []

            unit_start_sec = cursor_samples / SAMPLE_RATE
            sub_cursor_samples = cursor_samples

            for sub_i, res in enumerate(sub_results):
                gs = (res.graphemes or "").strip()
                ps = (res.phonemes or "").strip()
                audio_tensor = res.audio
                if audio_tensor is None:
                    continue
                if isinstance(audio_tensor, torch.Tensor):
                    chunk_np = audio_tensor.detach().cpu().numpy().astype(np.float32).flatten()
                else:
                    chunk_np = np.asarray(audio_tensor, dtype=np.float32).flatten()

                if chunk_np.size == 0:
                    continue

                sub_start_sec = sub_cursor_samples / SAMPLE_RATE
                sub_dur_sec = chunk_np.size / SAMPLE_RATE
                sub_end_sec = sub_start_sec + sub_dur_sec

                if getattr(res, "tokens", None):
                    sub_raw_words: List[Dict[str, Any]] = []
                    for tk in res.tokens:
                        w_text = getattr(tk, "text", "")
                        if not w_text:
                            continue
                        w_start = getattr(tk, "start_ts", None)
                        w_end = getattr(tk, "end_ts", None)
                        sub_raw_words.append({
                            "word": w_text,
                            "whitespace": getattr(tk, "whitespace", " "),
                            "start": round(sub_start_sec + float(w_start), 3) if w_start is not None else None,
                            "end": round(sub_start_sec + float(w_end), 3) if w_end is not None else None,
                        })
                    unit_words_meta.extend(
                        sanitize_segment_word_timings(sub_raw_words, seg_start=sub_start_sec, seg_end=sub_end_sec)
                    )

                if gs:
                    unit_gs_parts.append(gs)
                if ps:
                    unit_ps_parts.append(ps)
                unit_audio_parts.append(chunk_np)
                sub_cursor_samples += chunk_np.size

                if sub_i < len(sub_results) - 1 and intra_para_pause_samples > 0:
                    unit_audio_parts.append(intra_para_pause_array)
                    sub_cursor_samples += intra_para_pause_samples

            if not unit_audio_parts:
                continue

            unit_audio = np.concatenate(unit_audio_parts, axis=0)
            unit_dur_sec = unit_audio.size / SAMPLE_RATE
            unit_end_sec = unit_start_sec + unit_dur_sec

            full_unit_text = units[unit_idx] if unit_idx < len(units) else " ".join(unit_gs_parts)
            segments_meta.append({
                "index": seg_index,
                "unit_type": chunk_mode,
                "voice": voice,
                "voice_name": voice_name,
                "voice_color": voice_color_meta["color"],
                "text": full_unit_text,
                "phonemes": " ".join(unit_ps_parts),
                "start": round(unit_start_sec, 3),
                "end": round(unit_end_sec, 3),
                "duration": round(unit_dur_sec, 3),
                "words": unit_words_meta,
            })

            audio_chunks.append(unit_audio)
            cursor_samples += unit_audio.size
            if inter_unit_pause_samples > 0:
                audio_chunks.append(inter_unit_pause_array)
                cursor_samples += inter_unit_pause_samples
            seg_index += 1

        if not audio_chunks:
            return _write_silent_skip_response("No pronounceable phonemes in unit; skipped cleanly.")

        if len(audio_chunks) > 1 and inter_unit_pause_samples > 0:
            audio_chunks.pop()
            cursor_samples -= inter_unit_pause_samples

        full_audio = np.concatenate(audio_chunks, axis=0)
        peak = float(np.max(np.abs(full_audio))) if full_audio.size > 0 else 0.0
        if peak > 0.99:
            full_audio = full_audio * (0.98 / peak)

        update_task_progress(
            task_id,
            96.0,
            "Writing lossless 24kHz 16-bit PCM WAV master…",
            f"Saving audio & phoneme alignment metadata ({round(full_audio.size / SAMPLE_RATE, 1)}s audio)",
            total_units,
            total_units,
        )

        sf.write(str(wav_path), full_audio, SAMPLE_RATE, subtype="PCM_16")
        total_duration = round(full_audio.size / SAMPLE_RATE, 3)
        elapsed_sec = round(time.time() - t0, 3)
        joined_text = "\n\n".join(units)

        rtf_val = round(elapsed_sec / max(0.01, total_duration), 3)
        self._total_syntheses += 1
        self._last_synthesis_ts = time.time()
        self._last_rtf = rtf_val
        self._last_duration_sec = total_duration
        stream_url = f"/api/audio/{job_id}/stream.wav"

        meta = {
            "job_id": job_id,
            "model": MODEL_REPO_ID,
            "chunk_mode": chunk_mode,
            "strip_markdown": bool(strip_markdown),
            "voice": voice,
            "voice_name": voice_name,
            "voice_color": voice_color_meta["color"],
            "voice_info": voice_info,
            "blend_voice": blend_voice if (blend_voice and blend_weight > 0) else None,
            "blend_weight": round(float(blend_weight or 0.0), 2) if blend_voice else 0.0,
            "speed": speed,
            "sample_rate": SAMPLE_RATE,
            "duration_sec": total_duration,
            "duration_seconds": total_duration,
            "synthesis_time_sec": elapsed_sec,
            "rtf": rtf_val,
            "char_count": len(joined_text),
            "word_count": len(joined_text.split()),
            "segments": segments_meta,
            "audio_url": stream_url,
            "wav_url": stream_url,
            "audio_wav_url": stream_url,
            "downloads": {
                "full_mp3": f"/api/audio/{job_id}/download?quality=full_mp3",
                "low_mp3": f"/api/audio/{job_id}/download?quality=low_mp3",
                "full_wav": f"/api/audio/{job_id}/download?quality=full_wav",
            },
            "cached": False,
        }
        meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
        update_task_progress(
            task_id,
            100.0,
            f"Kokoro-82M synthesis complete ({total_duration}s audio in {elapsed_sec}s)",
            f"Ready to play & export ({len(segments_meta)} {unit_label.lower()}{'s' if len(segments_meta) != 1 else ''})",
            total_units,
            total_units,
            status="completed",
        )
        return meta


ENGINE = KokoroEngine()


# ==============================================================================
# TEXT NORMALIZATION, PARAGRAPH / LINE & CHAPTER EXTRACTION ENGINE
# ==============================================================================
def clean_ligatures_and_control_chars(text: str) -> str:
    if not text:
        return ""
    replacements = {
        "\ufb00": "ff",
        "\ufb01": "fi",
        "\ufb02": "fl",
        "\ufb03": "ffi",
        "\ufb04": "ffl",
        "\u00ad": "",
        "\u200b": "",
        "\r\n": "\n",
        "\r": "\n",
    }
    for k, v in replacements.items():
        text = text.replace(k, v)
    text = re.sub(r"([a-zA-Z])-\n\s*([a-z])", r"\1\2", text)
    return text.strip()


HEADING_PATTERN = re.compile(
    r"^(?:"
    r"#{1,6}\s+.+|"
    r"(?:Chapter|CHAPTER|Part|PART|Section|SECTION|Book|BOOK|Act|ACT|Episode|EPISODE|Prologue|PROLOGUE|Epilogue|EPILOGUE|Introduction|INTRODUCTION|Appendix|APPENDIX)\b[\s\dIVXLCDM.:—\-]*.*|"
    r"Page\s+\d+\s*[·—\-:]\s+.+|"
    r"(?:[IVXLCDM]+|\d+(?:\.\d+)?)\s*[.:\-—]\s+[A-Z][^\n]{2,90}"
    r")$"
)


def is_heading_candidate(text: str) -> bool:
    s = (text or "").strip()
    if not s or len(s) > 120:
        return False
    return bool(HEADING_PATTERN.match(s))


def clean_chapter_title(raw_title: str) -> str:
    t = strip_markdown_formatting(raw_title, enabled=True)
    t = re.sub(r"^\s*#{1,6}\s*", "", t).strip()
    return t[:110] if t else "Untitled Section"


def extract_text_paragraphs_and_lines(text: str) -> Tuple[List[str], List[str]]:
    cleaned = clean_ligatures_and_control_chars(text)
    if not cleaned:
        return [], []

    # Remove standalone Markdown horizontal rules / scene dividers (e.g. `---`, `***`, `___`)
    # so they never produce non-speakable empty paragraph cards.
    cleaned_no_hr = re.sub(r"(?m)^\s*(?:[-*_—–=~•✦❖]\s*){3,}\s*$", "\n\n", cleaned)
    cleaned_with_heading_breaks = re.sub(r"(?m)^(\s*#{1,6}\s+.+)$", r"\n\n\1\n\n", cleaned_no_hr)

    if re.search(r"\n\s*\n", cleaned_with_heading_breaks):
        raw_paras = re.split(r"\n\s*\n+", cleaned_with_heading_breaks)
        paragraphs = []
        for rp in raw_paras:
            unwrapped = re.sub(r"\s*\n\s*", " ", rp.strip())
            unwrapped = re.sub(r"[ \t]+", " ", unwrapped).strip()
            if unwrapped and not is_decorative_divider_only(unwrapped):
                paragraphs.append(unwrapped)
    else:
        raw_lines_list = [
            re.sub(r"[ \t]+", " ", l.strip())
            for l in cleaned_no_hr.split("\n")
            if l.strip() and not is_decorative_divider_only(l)
        ]
        paragraphs = []
        cur: List[str] = []
        for line in raw_lines_list:
            if is_heading_candidate(line):
                if cur:
                    paragraphs.append(" ".join(cur))
                    cur = []
                paragraphs.append(line)
                continue
            if not cur:
                cur.append(line)
                continue
            prev = cur[-1]
            if not re.search(r'[.!?…:"”\']$', prev):
                cur.append(line)
            else:
                paragraphs.append(" ".join(cur))
                cur = [line]
        if cur:
            paragraphs.append(" ".join(cur))

    lines: List[str] = []
    raw_lines_explicit = [
        re.sub(r"[ \t]+", " ", l.strip())
        for l in cleaned_no_hr.split("\n")
        if l.strip() and not is_decorative_divider_only(l)
    ]
    if len(raw_lines_explicit) > len(paragraphs):
        for rl in raw_lines_explicit:
            parts = [p.strip() for p in re.split(r'(?<=[.!?…])\s+', rl) if p.strip()]
            lines.extend(parts)
    else:
        for para in paragraphs:
            parts = [p.strip() for p in re.split(r'(?<=[.!?…])\s+', para) if p.strip()]
            lines.extend(parts)

    return paragraphs, lines


def extract_pdf_page_paragraphs_and_lines(page: Any) -> Tuple[List[str], List[str], List[Dict[str, Any]]]:
    d = page.get_text("dict")
    raw_lines: List[Dict[str, Any]] = []
    for block in d.get("blocks", []):
        if block.get("type", 0) != 0:
            continue
        for line in block.get("lines", []):
            bbox = line.get("bbox", (0, 0, 0, 0))
            spans = line.get("spans", [])
            raw_line_txt = "".join(s.get("text", "") for s in spans)
            line_txt = clean_ligatures_and_control_chars(raw_line_txt)
            line_txt = re.sub(r"[ \t]+", " ", line_txt).strip()
            if not line_txt:
                continue
            avg_size = max((float(s.get("size", 12.0)) for s in spans), default=12.0)
            raw_lines.append({
                "x0": float(bbox[0]),
                "y0": float(bbox[1]),
                "x1": float(bbox[2]),
                "y1": float(bbox[3]),
                "w": float(bbox[2] - bbox[0]),
                "h": max(float(bbox[3] - bbox[1]), avg_size),
                "size": avg_size,
                "text": line_txt,
            })

    if not raw_lines:
        return [], [], []

    positive_gaps = []
    for i in range(1, len(raw_lines)):
        g = raw_lines[i]["y0"] - raw_lines[i - 1]["y1"]
        if -2.0 <= g <= raw_lines[i - 1]["h"] * 1.5:
            positive_gaps.append(g)
    base_gap = min(positive_gaps) if positive_gaps else 4.0
    max_width = max((l["w"] for l in raw_lines), default=400.0)
    sizes_sorted = sorted(l["size"] for l in raw_lines)
    median_size = sizes_sorted[len(sizes_sorted) // 2] if sizes_sorted else 12.0

    paragraphs: List[str] = []
    para_max_sizes: List[float] = []
    cur_para: List[str] = [raw_lines[0]["text"]]
    cur_max_size: float = raw_lines[0]["size"]

    for i in range(1, len(raw_lines)):
        prev = raw_lines[i - 1]
        curr = raw_lines[i]
        gap = curr["y0"] - prev["y1"]
        size_diff = abs(curr["size"] - prev["size"])
        is_bullet = bool(re.match(r"^([•\-\*▪▸]|\d+[\.\)])\s+", curr["text"]))
        is_large_gap = gap > max(base_gap + 4.2, prev["h"] * 0.42)
        is_short_end = (prev["w"] < max_width * 0.72) and bool(re.search(r'[.!?…:"”\']$', prev["text"]))

        if is_large_gap or size_diff > 1.2 or is_bullet or is_short_end:
            paragraphs.append(" ".join(cur_para))
            para_max_sizes.append(cur_max_size)
            cur_para = [curr["text"]]
            cur_max_size = curr["size"]
        else:
            if cur_para[-1].endswith("-") and re.match(r"^[a-z]", curr["text"]):
                cur_para[-1] = cur_para[-1][:-1] + curr["text"]
            else:
                cur_para.append(curr["text"])
            cur_max_size = max(cur_max_size, curr["size"])

    if cur_para:
        paragraphs.append(" ".join(cur_para))
        para_max_sizes.append(cur_max_size)

    page_headings: List[Dict[str, Any]] = []
    for p_idx, p_text in enumerate(paragraphs):
        p_size = para_max_sizes[p_idx] if p_idx < len(para_max_sizes) else median_size
        is_footer = bool(re.search(r"Page\s+\d+\s+of\s+\d+", p_text, re.IGNORECASE)) and p_idx == len(paragraphs) - 1
        if is_footer:
            continue
        if (p_size >= median_size + 1.2 and len(p_text) <= 120) or is_heading_candidate(p_text):
            page_headings.append({
                "paragraph_index": p_idx,
                "title": clean_chapter_title(p_text),
                "size": p_size,
            })

    lines = [l["text"] for l in raw_lines]
    return paragraphs, lines, page_headings


def build_chapters_for_document(
    pages: List[Dict[str, Any]],
    pdf_toc: Optional[List[Any]] = None,
) -> List[Dict[str, Any]]:
    if not pages:
        return []

    total_pages = len(pages)
    anchors: List[Dict[str, Any]] = []

    if pdf_toc:
        for entry in pdf_toc:
            if len(entry) >= 3:
                lvl, raw_title, p_num = int(entry[0]), str(entry[1]).strip(), int(entry[2])
                p_num = max(1, min(total_pages, p_num))
                title = clean_chapter_title(raw_title)
                if not title:
                    continue
                p_obj = pages[p_num - 1]
                start_para_idx = 0
                for idx, para in enumerate(p_obj.get("paragraphs") or []):
                    if title.lower()[:25] in para.lower():
                        start_para_idx = idx
                        break
                anchors.append({
                    "level": max(1, min(3, lvl)),
                    "title": title,
                    "start_page": p_num,
                    "start_paragraph_index": start_para_idx,
                })

    if not anchors:
        for p_obj in pages:
            p_num = p_obj["page_number"]
            p_headings = p_obj.get("headings") or []
            if p_headings:
                for h in p_headings:
                    anchors.append({
                        "level": 1,
                        "title": h["title"],
                        "start_page": p_num,
                        "start_paragraph_index": h.get("paragraph_index", 0),
                    })
            else:
                for idx, para in enumerate(p_obj.get("paragraphs") or []):
                    if is_heading_candidate(para):
                        anchors.append({
                            "level": 2 if para.lstrip().startswith("##") else 1,
                            "title": clean_chapter_title(para),
                            "start_page": p_num,
                            "start_paragraph_index": idx,
                        })

    deduped: List[Dict[str, Any]] = []
    for a in anchors:
        if deduped and deduped[-1]["start_page"] == a["start_page"] and deduped[-1]["start_paragraph_index"] == a["start_paragraph_index"]:
            continue
        deduped.append(a)
    anchors = deduped

    if not anchors:
        return []

    chapters: List[Dict[str, Any]] = []
    for c_idx, anchor in enumerate(anchors):
        start_p = anchor["start_page"]
        start_para = anchor["start_paragraph_index"]

        if c_idx + 1 < len(anchors):
            next_a = anchors[c_idx + 1]
            end_p = next_a["start_page"]
            end_para_exclusive = next_a["start_paragraph_index"]
        else:
            end_p = total_pages
            end_para_exclusive = None

        chap_paras: List[str] = []
        chap_lines: List[str] = []
        para_refs: List[Dict[str, int]] = []

        for p_num in range(start_p, end_p + 1):
            p_obj = pages[p_num - 1]
            p_paras = p_obj.get("paragraphs") or []
            p_lines = p_obj.get("lines") or []

            p_start = start_para if p_num == start_p else 0
            if p_num == end_p and end_para_exclusive is not None:
                p_end = end_para_exclusive
            else:
                p_end = len(p_paras)

            if p_num == start_p and p_end <= p_start and p_start < len(p_paras):
                p_end = len(p_paras)

            for pi in range(p_start, min(len(p_paras), p_end)):
                chap_paras.append(p_paras[pi])
                para_refs.append({"page_number": p_num, "paragraph_index": pi})

            if p_num == start_p and p_num == end_p and end_para_exclusive is not None:
                for para_str in chap_paras:
                    parts = [pt.strip() for pt in re.split(r'(?<=[.!?…])\s+', para_str) if pt.strip()]
                    chap_lines.extend(parts)
            else:
                chap_lines.extend(p_lines)

        actual_end_page = para_refs[-1]["page_number"] if para_refs else start_p
        joined_chap = "\n\n".join(chap_paras)
        words = len(joined_chap.split()) if joined_chap else 0

        chapters.append({
            "chapter_number": c_idx + 1,
            "title": anchor["title"],
            "level": anchor.get("level", 1),
            "start_page": start_p,
            "end_page": actual_end_page,
            "start_paragraph_index": start_para,
            "paragraph_count": len(chap_paras),
            "line_count": len(chap_lines),
            "word_count": words,
            "estimated_seconds": round(words / 2.6, 1) if words > 0 else 0.0,
            "paragraphs": chap_paras,
            "lines": chap_lines,
            "text": joined_chap,
            "paragraph_refs": para_refs,
        })

    return chapters


DOCUMENTS_STORE: Dict[str, Dict[str, Any]] = {}


def build_page_dict(
    page_number: int,
    paragraphs: List[str],
    lines: List[str],
    has_visual_page: bool,
    headings: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    clean_text = "\n\n".join(paragraphs)
    words = len(clean_text.split()) if clean_text else 0
    snippet_clean = re.sub(r"\s+", " ", strip_markdown_formatting(clean_text, enabled=True)).strip()
    return {
        "page_number": page_number,
        "text": clean_text,
        "snippet": snippet_clean[:96] if snippet_clean else "",
        "raw_char_count": len(clean_text),
        "char_count": len(clean_text),
        "word_count": words,
        "estimated_seconds": round(words / 2.6, 1) if words > 0 else 0.0,
        "paragraphs": paragraphs,
        "lines": lines,
        "sentences": paragraphs,
        "paragraph_count": len(paragraphs),
        "line_count": len(lines),
        "headings": headings or [],
        "has_visual_page": has_visual_page,
        "is_empty": len(clean_text.strip()) == 0,
    }


def parse_pdf_file(file_path: Path, task_id: Optional[str] = None) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    pages_out: List[Dict[str, Any]] = []
    try:
        import pymupdf
        update_task_progress(task_id, 12.0, "Opening PDF document", f"Reading PDF structure from {file_path.name}…")
        doc = pymupdf.open(str(file_path))
        toc = doc.get_toc() or []
        total_p = max(1, len(doc))
        for idx in range(len(doc)):
            pct = 15.0 + ((idx + 1) / total_p) * 68.0
            update_task_progress(
                task_id,
                pct,
                f"Extracting PDF Page {idx + 1} of {total_p}",
                f"Parsing paragraphs, lines & headings on page {idx + 1}/{total_p}…",
                current_step=idx + 1,
                total_steps=total_p,
            )
            page = doc[idx]
            paragraphs, lines, page_headings = extract_pdf_page_paragraphs_and_lines(page)
            pages_out.append(build_page_dict(idx + 1, paragraphs, lines, has_visual_page=True, headings=page_headings))
        doc.close()
        update_task_progress(
            task_id, 88.0,
            "Detecting chapters & table of contents",
            f"Indexing chapters across {total_p} pages…",
            current_step=total_p,
            total_steps=total_p,
        )
        chapters = build_chapters_for_document(pages_out, pdf_toc=toc)
        return pages_out, chapters
    except ImportError:
        from pypdf import PdfReader
        update_task_progress(task_id, 12.0, "Opening PDF document", f"Reading PDF structure from {file_path.name}…")
        reader = PdfReader(str(file_path))
        total_p = max(1, len(reader.pages))
        for idx, page in enumerate(reader.pages):
            pct = 15.0 + ((idx + 1) / total_p) * 68.0
            update_task_progress(
                task_id,
                pct,
                f"Extracting PDF Page {idx + 1} of {total_p}",
                f"Extracting text blocks from page {idx + 1}/{total_p}…",
                current_step=idx + 1,
                total_steps=total_p,
            )
            raw_text = page.extract_text() or ""
            paragraphs, lines = extract_text_paragraphs_and_lines(raw_text)
            pages_out.append(build_page_dict(idx + 1, paragraphs, lines, has_visual_page=False))
        update_task_progress(task_id, 88.0, "Detecting chapters", f"Indexing chapters across {total_p} pages…")
        chapters = build_chapters_for_document(pages_out)
        return pages_out, chapters


def parse_docx_file(file_path: Path, task_id: Optional[str] = None) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    import docx
    update_task_progress(task_id, 18.0, "Opening Word document (.docx)", f"Reading paragraphs from {file_path.name}…")
    d = docx.Document(str(file_path))
    formatted_paras: List[str] = []
    total_p = max(1, len(d.paragraphs))
    for i, p in enumerate(d.paragraphs):
        if i % 25 == 0 or i == total_p - 1:
            update_task_progress(
                task_id,
                20.0 + ((i + 1) / total_p) * 50.0,
                "Extracting Word document paragraphs",
                f"Processed {i + 1} of {total_p} paragraphs…",
                current_step=i + 1,
                total_steps=total_p,
            )
        txt = (p.text or "").strip()
        if not txt:
            continue
        style_name = (getattr(p.style, "name", "") or "").lower()
        if "heading" in style_name or "title" in style_name:
            formatted_paras.append(f"# {txt}")
        else:
            formatted_paras.append(txt)
    full_text = "\n\n".join(formatted_paras)
    update_task_progress(task_id, 78.0, "Paginating Word document", "Organizing paragraphs into reader pages & chapters…")
    pages = paginate_plain_text(full_text, words_per_page=320)
    chapters = build_chapters_for_document(pages)
    return pages, chapters


def convert_rtf_to_plain_text(file_path: Optional[Path] = None, raw_rtf: Optional[str] = None) -> str:
    """Converts Rich Text Format (.rtf) cleanly into Unicode plain text via macOS `textutil` (with pure-Python fallback)."""
    if file_path and file_path.exists():
        try:
            proc = subprocess.run(
                ["/usr/bin/textutil", "-convert", "txt", "-stdout", str(file_path)],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if proc.returncode == 0 and proc.stdout.strip():
                return proc.stdout
        except Exception:
            pass

    src = raw_rtf if raw_rtf is not None else (file_path.read_text(encoding="utf-8", errors="replace") if file_path else "")
    if not src:
        return ""
    if raw_rtf and Path("/usr/bin/textutil").exists():
        try:
            proc = subprocess.run(
                ["/usr/bin/textutil", "-convert", "txt", "-stdin", "-stdout", "-format", "rtf"],
                input=src,
                capture_output=True,
                text=True,
                timeout=20,
            )
            if proc.returncode == 0 and proc.stdout.strip():
                return proc.stdout
        except Exception:
            pass

    # Pure-Python fallback RTF stripper
    out = re.sub(r"\\par[d]?\b", "\n\n", src)
    out = re.sub(r"\\line\b", "\n", out)
    out = re.sub(r"\\'([0-9a-fA-F]{2})", lambda m: bytes.fromhex(m.group(1)).decode("cp1252", errors="ignore"), out)
    out = re.sub(r"\\u(-?\d+)\??", lambda m: chr(int(m.group(1)) & 0xFFFF), out)
    out = re.sub(r"\{\\\*\\[^{}]+\}", "", out)
    out = re.sub(r"\{\\(?:fonttbl|colortbl|stylesheet|info)[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", "", out)
    out = re.sub(r"\\[a-zA-Z]+-?\d*\s?", "", out)
    out = out.replace("{", "").replace("}", "")
    return out.strip()


def parse_rtf_file(file_path: Path, task_id: Optional[str] = None) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    update_task_progress(task_id, 24.0, "Converting Rich Text (.rtf) document", f"Extracting clean Unicode prose from {file_path.name}…")
    plain_txt = convert_rtf_to_plain_text(file_path=file_path)
    update_task_progress(task_id, 72.0, "Paginating Rich Text document", "Structuring pages and chapters…")
    pages = paginate_plain_text(plain_txt, words_per_page=320)
    chapters = build_chapters_for_document(pages)
    return pages, chapters


def paginate_plain_text(full_text: str, words_per_page: int = 320) -> List[Dict[str, Any]]:
    paragraphs_all, _ = extract_text_paragraphs_and_lines(full_text)
    if not paragraphs_all:
        return [build_page_dict(1, [], [], has_visual_page=False)]

    if "\f" in full_text:
        raw_pages = [p for p in full_text.split("\f") if p.strip()]
    else:
        raw_pages = []
        current_pars: List[str] = []
        current_words = 0
        for p in paragraphs_all:
            p_words = len(p.split())
            if current_pars and (current_words + p_words > words_per_page):
                raw_pages.append("\n\n".join(current_pars))
                current_pars = [p]
                current_words = p_words
            else:
                current_pars.append(p)
                current_words += p_words
        if current_pars:
            raw_pages.append("\n\n".join(current_pars))

    pages_out: List[Dict[str, Any]] = []
    for idx, page_txt in enumerate(raw_pages):
        paras, lines = extract_text_paragraphs_and_lines(page_txt)
        headings = []
        for p_i, p_str in enumerate(paras):
            if is_heading_candidate(p_str):
                headings.append({"paragraph_index": p_i, "title": clean_chapter_title(p_str)})
        pages_out.append(build_page_dict(idx + 1, paras, lines, has_visual_page=False, headings=headings))
    return pages_out


def build_doc_record_from_text(
    raw_text: str,
    filename: str = "Document.md",
    filepath_override: Optional[str] = None,
    doc_id_override: Optional[str] = None,
) -> Dict[str, Any]:
    if raw_text and raw_text.lstrip().startswith("{\\rtf"):
        raw_text = convert_rtf_to_plain_text(raw_rtf=raw_text)
    clean_fname = re.sub(r"^\d{9,12}_", "", filename or "Document.md")
    pages = paginate_plain_text(raw_text)
    chapters = build_chapters_for_document(pages)
    ext = Path(clean_fname).suffix.lower() or ".md"
    sha = hashlib.sha256(raw_text.encode("utf-8")).hexdigest()
    doc_id = doc_id_override or hashlib.sha256(f"text:{clean_fname}:{sha}:v3.0".encode("utf-8")).hexdigest()[:16]
    total_words = sum(p["word_count"] for p in pages)
    total_chars = sum(p["char_count"] for p in pages)
    total_paragraphs = sum(p["paragraph_count"] for p in pages)
    total_lines = sum(p["line_count"] for p in pages)
    non_empty_pages = sum(1 for p in pages if not p["is_empty"])

    doc_record = {
        "doc_id": doc_id,
        "filename": clean_fname,
        "filepath": filepath_override or f"library://{clean_fname}",
        "extension": ext,
        "is_pdf": (ext == ".pdf"),
        "doc_type": "Markdown Document (.md)" if ext == ".md" else ("Rich Text Document (.rtf)" if ext == ".rtf" else f"Text Document ({ext})"),
        "size_bytes": len(raw_text.encode("utf-8")),
        "mtime": int(time.time()),
        "sha256": sha,
        "total_pages": len(pages),
        "non_empty_pages": non_empty_pages,
        "total_chapters": len(chapters),
        "has_chapters": len(chapters) > 0,
        "total_paragraphs": total_paragraphs,
        "total_lines": total_lines,
        "total_words": total_words,
        "total_chars": total_chars,
        "estimated_total_seconds": round(total_words / 2.6, 1),
        "pages": pages,
        "chapters": chapters,
        "loaded_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    DOCUMENTS_STORE[doc_id] = doc_record
    return doc_record


RECENT_FILES_FILE = CACHE_DIR / "recent_files.json"


def compute_file_sha256(path_obj: Path) -> str:
    h = hashlib.sha256()
    with path_obj.open("rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def record_recent_file(doc_record: Dict[str, Any]) -> None:
    try:
        filepath = doc_record.get("filepath", "")
        if not filepath or filepath.startswith("text://") or filepath.startswith("library://"):
            return
        existing: List[Dict[str, Any]] = []
        if RECENT_FILES_FILE.exists():
            try:
                existing = json.loads(RECENT_FILES_FILE.read_text(encoding="utf-8"))
                if not isinstance(existing, list):
                    existing = []
            except Exception:
                existing = []

        fname = doc_record.get("filename") or Path(filepath).name
        fext = doc_record.get("extension") or Path(filepath).suffix.lower()
        entry = {
            "path": filepath,
            "filepath": filepath,
            "name": fname,
            "filename": fname,
            "ext": fext,
            "extension": fext,
            "size_bytes": doc_record.get("size_bytes", 0),
            "sha256": doc_record.get("sha256", ""),
            "total_pages": doc_record.get("total_pages", 1),
            "total_chapters": doc_record.get("total_chapters", 0),
            "total_words": doc_record.get("total_words", 0),
            "last_opened": time.strftime("%Y-%m-%d %H:%M:%S"),
            "last_opened_ts": int(time.time()),
        }
        filtered = [x for x in existing if x.get("path") != filepath]
        filtered.insert(0, entry)
        RECENT_FILES_FILE.write_text(json.dumps(filtered[:25], indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        print(f"Warning recording recent file: {e}")


def get_recent_files_list() -> List[Dict[str, Any]]:
    if not RECENT_FILES_FILE.exists():
        return []
    try:
        raw = json.loads(RECENT_FILES_FILE.read_text(encoding="utf-8"))
        if not isinstance(raw, list):
            return []
        out: List[Dict[str, Any]] = []
        for item in raw:
            p_str = item.get("path") or item.get("filepath") or ""
            if not p_str:
                continue
            p_obj = Path(p_str)
            exists = p_obj.exists() and p_obj.is_file()
            status = "available"
            cur_size = item.get("size_bytes", 0)
            if not exists:
                status = "missing"
            else:
                try:
                    st = p_obj.stat()
                    cur_size = st.st_size
                    if item.get("size_bytes") and st.st_size != item.get("size_bytes"):
                        status = "changed"
                except Exception:
                    pass
            fname = item.get("name") or item.get("filename") or p_obj.name
            fext = item.get("ext") or item.get("extension") or p_obj.suffix.lower()
            out.append({
                **item,
                "path": p_str,
                "filepath": p_str,
                "name": fname,
                "filename": fname,
                "ext": fext,
                "extension": fext,
                "exists": exists,
                "changed": status == "changed",
                "status": status,
                "current_size_bytes": cur_size,
            })
        return out
    except Exception:
        return []


def clear_recent_files_list() -> None:
    try:
        if RECENT_FILES_FILE.exists():
            RECENT_FILES_FILE.write_text("[]", encoding="utf-8")
    except Exception:
        pass


def load_document_from_path(
    file_path_str: str,
    task_id: Optional[str] = None,
    auto_link_project: bool = True,
    display_filename: Optional[str] = None,
) -> Dict[str, Any]:
    check_task_cancelled(task_id)
    update_task_progress(task_id, 5.0, "Locating document on disk", f"Validating path: {Path(file_path_str).name}…")
    cleaned_path = file_path_str.strip().strip("'").strip('"')
    expanded = os.path.expanduser(cleaned_path)
    path_obj = Path(expanded).resolve()

    if not path_obj.exists():
        update_task_progress(task_id, 100.0, "File not found", f"Could not find {expanded}", status="error")
        raise FileNotFoundError(f"File not found: {expanded}")
    if not path_obj.is_file():
        update_task_progress(task_id, 100.0, "Invalid path", f"Path is a directory: {expanded}", status="error")
        raise ValueError(f"Path is a directory, not a file: {expanded}")

    ext = path_obj.suffix.lower()
    stat = path_obj.stat()
    file_sha256 = compute_file_sha256(path_obj)
    doc_id = hashlib.sha256(f"{path_obj}:{stat.st_mtime}:{stat.st_size}:v3.0".encode("utf-8")).hexdigest()[:16]

    effective_filename = (display_filename or "").strip() or path_obj.name
    if path_obj.parent == CACHE_DIR:
        effective_filename = re.sub(r"^\d{9,12}_", "", effective_filename)

    if doc_id in DOCUMENTS_STORE:
        cached_doc = DOCUMENTS_STORE[doc_id]
        if display_filename:
            cached_doc["filename"] = effective_filename
        record_recent_file(cached_doc)
        if auto_link_project:
            proj_info = ensure_project_for_document(cached_doc)
            cached_doc["project_id"] = proj_info["project"]["project_id"]
            cached_doc["version_id"] = proj_info["active_version"]["version_id"]
            cached_doc["version_label"] = proj_info["active_version"]["version_label"]
            cached_doc["project_theme_id"] = proj_info["project"].get("theme_id", "system_auto")
        update_task_progress(
            task_id,
            100.0,
            f"Loaded {cached_doc['filename']} (Instant Cache)",
            f"{cached_doc['total_pages']} pages · {cached_doc['total_paragraphs']} paragraphs · {cached_doc['total_words']:,} words",
            current_step=cached_doc["total_pages"],
            total_steps=cached_doc["total_pages"],
            status="completed",
        )
        return cached_doc

    if ext == ".pdf":
        pages, chapters = parse_pdf_file(path_obj, task_id=task_id)
        doc_type = "PDF Document"
    elif ext == ".docx":
        pages, chapters = parse_docx_file(path_obj, task_id=task_id)
        doc_type = "Word Document (.docx)"
    elif ext == ".rtf":
        pages, chapters = parse_rtf_file(path_obj, task_id=task_id)
        doc_type = "Rich Text Document (.rtf)"
    elif ext in {".html", ".htm"}:
        update_task_progress(task_id, 25.0, "Parsing HTML document", f"Cleaning HTML tags and extracting text from {effective_filename}…")
        raw_html = path_obj.read_text(encoding="utf-8", errors="replace")
        stripped = re.sub(r"<script[\s\S]*?</script>", "", raw_html, flags=re.IGNORECASE)
        stripped = re.sub(r"<style[\s\S]*?</style>", "", stripped, flags=re.IGNORECASE)
        stripped = re.sub(r"<(h1|h2|h3)[^>]*>([\s\S]*?)</\1>", r"\n\n# \2\n\n", stripped, flags=re.IGNORECASE)
        stripped = re.sub(r"</(p|div|h4|li|tr|section|article)>", "\n\n", stripped, flags=re.IGNORECASE)
        stripped = re.sub(r"<br\s*/?>", "\n", stripped, flags=re.IGNORECASE)
        stripped = re.sub(r"<[^>]+>", " ", stripped)
        update_task_progress(task_id, 70.0, "Paginating HTML content", "Building pages and chapters…")
        pages = paginate_plain_text(html.unescape(stripped))
        chapters = build_chapters_for_document(pages)
        doc_type = "HTML Document"
    else:
        update_task_progress(task_id, 25.0, "Reading text document", f"Reading and segmenting {effective_filename}…")
        raw_text = path_obj.read_text(encoding="utf-8", errors="replace")
        if raw_text.lstrip().startswith("{\\rtf"):
            raw_text = convert_rtf_to_plain_text(file_path=path_obj)
            doc_type = "Rich Text Document (.rtf)"
        else:
            doc_type = "Markdown Document (.md)" if ext == ".md" else f"Text Document ({ext or '.txt'})"
        update_task_progress(task_id, 65.0, "Paginating paragraphs & lines", f"Structuring pages and chapters for {effective_filename}…")
        pages = paginate_plain_text(raw_text)
        chapters = build_chapters_for_document(pages)

    total_words = sum(p["word_count"] for p in pages)
    total_chars = sum(p["char_count"] for p in pages)
    total_paragraphs = sum(p["paragraph_count"] for p in pages)
    total_lines = sum(p["line_count"] for p in pages)
    non_empty_pages = sum(1 for p in pages if not p["is_empty"])

    doc_record = {
        "doc_id": doc_id,
        "filename": effective_filename,
        "filepath": str(path_obj),
        "extension": ext,
        "is_pdf": (ext == ".pdf"),
        "doc_type": doc_type,
        "size_bytes": stat.st_size,
        "mtime": int(stat.st_mtime),
        "sha256": file_sha256,
        "total_pages": len(pages),
        "non_empty_pages": non_empty_pages,
        "total_chapters": len(chapters),
        "has_chapters": len(chapters) > 0,
        "total_paragraphs": total_paragraphs,
        "total_lines": total_lines,
        "total_words": total_words,
        "total_chars": total_chars,
        "estimated_total_seconds": round(total_words / 2.6, 1),
        "pages": pages,
        "chapters": chapters,
        "loaded_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    DOCUMENTS_STORE[doc_id] = doc_record
    record_recent_file(doc_record)
    if auto_link_project:
        proj_info = ensure_project_for_document(doc_record)
        doc_record["project_id"] = proj_info["project"]["project_id"]
        doc_record["version_id"] = proj_info["active_version"]["version_id"]
        doc_record["version_label"] = proj_info["active_version"]["version_label"]
        doc_record["project_theme_id"] = proj_info["project"].get("theme_id", "system_auto")

    update_task_progress(
        task_id,
        100.0,
        f"Loaded {path_obj.name}",
        f"{len(pages)} pages · {len(chapters)} chapters · {total_paragraphs} paragraphs · {total_words:,} words",
        current_step=len(pages),
        total_steps=len(pages),
        status="completed",
    )
    return doc_record


# ==============================================================================
# AUDIO TRANSCODING: FULL-RES MP3 (320k), LO-QUALITY MP3 (48k), FULL-RES WAV
# ==============================================================================
def convert_wav_to_mp3(wav_path: Path, quality: str, task_id: Optional[str] = None) -> Path:
    if quality == "full_mp3":
        mp3_path = wav_path.with_name(f"{wav_path.stem}_FullRes_320kbps.mp3")
        label_desc = "Full-Resolution 320 kbps Stereo MP3 (48kHz)"
        cmd = [
            "ffmpeg", "-y", "-i", str(wav_path),
            "-vn", "-ar", "48000", "-ac", "2",
            "-c:a", "libmp3lame", "-b:a", "320k",
            str(mp3_path)
        ]
    elif quality == "low_mp3":
        mp3_path = wav_path.with_name(f"{wav_path.stem}_LoQuality_48kbps.mp3")
        label_desc = "Compact Lo-Quality 48 kbps Mono MP3 (24kHz)"
        cmd = [
            "ffmpeg", "-y", "-i", str(wav_path),
            "-vn", "-ar", "24000", "-ac", "1",
            "-c:a", "libmp3lame", "-b:a", "48k",
            str(mp3_path)
        ]
    else:
        update_task_progress(
            task_id, 100.0,
            "Lossless WAV Ready",
            f"Prepared 24kHz PCM WAV ({wav_path.stat().st_size / 1024:.0f} KB)",
            current_step=1,
            total_steps=1,
            status="completed",
        )
        return wav_path

    if mp3_path.exists() and mp3_path.stat().st_size > 0:
        update_task_progress(
            task_id, 100.0,
            f"MP3 Ready ({label_desc})",
            f"Cached MP3 ready for download ({mp3_path.stat().st_size / 1024:.0f} KB)",
            current_step=2,
            total_steps=2,
            status="completed",
        )
        return mp3_path

    update_task_progress(
        task_id, 28.0,
        f"Transcoding to {label_desc}",
        "Running FFmpeg libmp3lame encoder on master WAV waveform…",
        current_step=1,
        total_steps=2,
    )
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0 or not mp3_path.exists():
        update_task_progress(task_id, 100.0, "FFmpeg MP3 encoding failed", proc.stderr[:200], status="error")
        raise RuntimeError(f"FFmpeg MP3 encoding failed: {proc.stderr[:400]}")

    update_task_progress(
        task_id, 100.0,
        f"Encoded {label_desc}",
        f"MP3 file ready ({mp3_path.stat().st_size / 1024:.0f} KB)",
        current_step=2,
        total_steps=2,
    )
    return mp3_path


# ==============================================================================
# SAMPLE DEMO PDF GENERATOR (WITH NATIVE TOC CHAPTERS)
# ==============================================================================
def ensure_sample_pdf() -> Path:
    sample_pdf_path = ASSETS_DIR / "KOKORO_ASSET_SampleGuide_v1.0_20261021_FINAL.pdf"
    if sample_pdf_path.exists() and sample_pdf_path.stat().st_size > 1000:
        try:
            import pymupdf
            doc = pymupdf.open(str(sample_pdf_path))
            if not doc.get_toc() and len(doc) >= 3:
                doc.set_toc([
                    [1, "Chapter 1: Welcome to Kokoro-82M Document Reader", 1],
                    [1, "Chapter 2: Continuous Page & Chapter Navigation", 2],
                    [1, "Chapter 3: Studio Audio Mastering & MP3 Exports", 3],
                ])
                doc.saveIncr()
            doc.close()
        except Exception:
            pass
        return sample_pdf_path

    try:
        import pymupdf
        doc = pymupdf.open()
        sample_pages = [
            (
                "Page 1 · Welcome to Kokoro-82M Document Reader",
                "Welcome to the Kokoro Eighty-Two Million Document and PDF Reader Studio.\n\n"
                "Kokoro is a frontier open-weight text-to-speech model with eighty-two million parameters, "
                "built on StyleTTS 2 and ISTFTNet architectures. Despite its compact footprint, it delivers "
                "remarkably natural prosody, emotional warmth, and crisp studio articulation.\n\n"
                "As this page is read aloud, each paragraph or line is highlighted in real time. You can click any "
                "paragraph on the page to jump directly to that moment, adjust the speaking rate with the "
                "speed chooser, or switch voices across nine languages."
            ),
            (
                "Page 2 · Page-by-Page Continuous Reading",
                "This is Page Two of your sample PDF document.\n\n"
                "When Auto-Advance Page-by-Page mode is enabled, the reader automatically pre-synthesizes "
                "upcoming paragraphs in the background while you listen. As soon as one paragraph finishes speaking, "
                "the reader continues seamlessly without waiting for the whole document.\n\n"
                "You can point this reader at any local PDF, Word document, Markdown file, or plain text file "
                "on your Mac using the Local File Path bar, the Native macOS Finder Picker, or Drag and Drop."
            ),
            (
                "Page 3 · Studio Audio Mastering & MP3 Exports",
                "This is Page Three, the final page of the sample guide.\n\n"
                "Every spoken page, plain text passage, or complete multi-page document can be downloaded "
                "immediately in three formats:\n\n"
                "First, Full Resolution MP3 at 320 kilobits per second for high-fidelity listening.\n\n"
                "Second, Full Resolution Lossless WAV at 24 kilohertz PCM for editing and archival.\n\n"
                "Third, Low-Quality Compact MP3 at 48 kilobits per second Mono, engineered for ultra-lightweight "
                "file sizes when exporting long books, lecture notes, or scripts for mobile listening."
            ),
        ]
        for p_idx, (heading, body) in enumerate(sample_pages, start=1):
            page = doc.new_page(width=595, height=842)
            page.draw_rect(pymupdf.Rect(40, 40, 555, 95), color=(0.08, 0.12, 0.22), fill=(0.08, 0.12, 0.22))
            page.insert_text(pymupdf.Point(56, 73), heading, fontsize=15, fontname="helv", color=(1, 1, 1))
            rect = pymupdf.Rect(52, 125, 543, 760)
            page.insert_textbox(rect, body, fontsize=13.5, fontname="helv", color=(0.12, 0.14, 0.18), lineheight=1.55)
            page.insert_text(
                pymupdf.Point(52, 800),
                f"Kokoro-82M Document & PDF Reader Studio  •  Page {p_idx} of {len(sample_pages)}",
                fontsize=9.5,
                fontname="helv",
                color=(0.45, 0.48, 0.55),
            )
        doc.set_toc([
            [1, "Chapter 1: Welcome to Kokoro-82M Document Reader", 1],
            [1, "Chapter 2: Continuous Page & Chapter Navigation", 2],
            [1, "Chapter 3: Studio Audio Mastering & MP3 Exports", 3],
        ])
        doc.save(str(sample_pdf_path))
        doc.close()
    except Exception as e:
        print(f"Warning: Could not generate sample PDF: {e}")
    return sample_pdf_path


# ==============================================================================
# PERSISTENT PROJECT LIBRARY, MULTI-VERSIONING, COMMENTS, OVERLAP & LLM EXPORT
# Note: All files in LIBRARY_DIR use hyphenated names (`proj-*.json`, `comments-*.json`,
# `snap-*.txt`, `custom-themes.json`) so they stay 100% separate from temp audio cache
# and workspace underscore-naming scanners.
# ==============================================================================
LIBRARY_LOCK = threading.Lock()
PROJECTS_INDEX_FILE = LIBRARY_DIR / "projects-index.json"
CUSTOM_THEMES_FILE = LIBRARY_DIR / "custom-themes.json"


def _project_file(project_id: str) -> Path:
    safe = re.sub(r"[^a-zA-Z0-9-]", "", project_id)
    return LIBRARY_DIR / f"proj-{safe}.json"


def _comments_file(project_id: str) -> Path:
    safe = re.sub(r"[^a-zA-Z0-9-]", "", project_id)
    return LIBRARY_DIR / f"comments-{safe}.json"


def _snapshot_file(version_id: str) -> Path:
    safe = re.sub(r"[^a-zA-Z0-9-]", "", version_id)
    return LIBRARY_DIR / f"snap-{safe}.txt"


def _clean_project_title(filename: str) -> str:
    clean_file = re.sub(r"^\d{9,12}_", "", (filename or "Untitled Document").strip())
    ext_m = re.search(r"\.(pdf|docx|doc|rtf|md|markdown|txt|html|htm)$", clean_file, flags=re.IGNORECASE)
    if ext_m:
        ext = "." + ext_m.group(1).lower()
        stem = clean_file[: ext_m.start()]
    else:
        ext = ""
        stem = clean_file

    # Strip workspace semantic prefix/suffix (`PROJECT_DOCTYPE_Descriptor_vX.Y_YYYYMMDD_STATUS`) into a clean human title
    m = re.match(r"^[A-Z0-9]+_[A-Z]+_([A-Za-z0-9]+?)_(v\d+(?:\.\d+)?)_\d{8}_[A-Z]+$", stem)
    if m:
        camel = m.group(1)
        ver_tag = m.group(2)
        spaced = re.sub(r"(?<!^)(?=[A-Z])", " ", camel).strip()
        ext_badge = f" [{ext.lstrip('.').upper()}]" if ext and ext not in (".md", ".pdf") else ""
        return f"{spaced} ({ver_tag}){ext_badge}"
    m2 = re.match(r"^[A-Z0-9]+\s+[A-Z]+\s+([A-Za-z0-9]+?)\s+(v\d+(?:\.\d+)?)(?:\s+\d{8}\s+[A-Z]+)?(?:\s*(\[[A-Z]+\]))?$", stem)
    if m2:
        camel = m2.group(1)
        ver_tag = m2.group(2)
        badge = f" {m2.group(3)}" if m2.group(3) else ""
        spaced = re.sub(r"(?<!^)(?=[A-Z])", " ", camel).strip()
        return f"{spaced} ({ver_tag}){badge}"
    cleaned = re.sub(r"^\d+_", "", stem).replace("_", " ").strip()
    return cleaned or clean_file


def _extract_full_text_from_doc(doc_record: Dict[str, Any]) -> str:
    return "\n\n\f\n\n".join((p.get("text") or "") for p in (doc_record.get("pages") or []))


def _default_custom_themes() -> List[Dict[str, Any]]:
    return [
        {
            "id": "custom_1",
            "slot": 1,
            "name": "Custom Theme 1 · Studio Plum",
            "persona": "Your Personal Custom Slot 1",
            "mode": "dark",
            "bg_base": "#15121e",
            "bg_panel": "#1d1929",
            "bg_card": "#252036",
            "text_primary": "#f5f2fc",
            "text_secondary": "#bdb4d4",
            "accent": "#b57cff",
            "secondary_accent": "#2dd4bf",
            "border": "#362f4d",
            "font_pair": "literary_serif",
            "radius_style": "soft",
            "texture": "matte",
            "line_height": 1.52,
        },
        {
            "id": "custom_2",
            "slot": 2,
            "name": "Custom Theme 2 · Warm Linen",
            "persona": "Your Personal Custom Slot 2",
            "mode": "light",
            "bg_base": "#f8f5ee",
            "bg_panel": "#f0ebe0",
            "bg_card": "#ffffff",
            "text_primary": "#1f1b16",
            "text_secondary": "#5c5346",
            "accent": "#8c3b2b",
            "secondary_accent": "#1e6b52",
            "border": "#dcd3c2",
            "font_pair": "editorial_classic",
            "radius_style": "crisp",
            "texture": "paper",
            "line_height": 1.55,
        },
        {
            "id": "custom_3",
            "slot": 3,
            "name": "Custom Theme 3 · Deep Aegean",
            "persona": "Your Personal Custom Slot 3",
            "mode": "dark",
            "bg_base": "#0d1821",
            "bg_panel": "#13222e",
            "bg_card": "#192c3b",
            "text_primary": "#eef6fb",
            "text_secondary": "#9cb6c9",
            "accent": "#38bdf8",
            "secondary_accent": "#f59e0b",
            "border": "#274257",
            "font_pair": "modern_humanist",
            "radius_style": "soft",
            "texture": "dots",
            "line_height": 1.48,
        },
        {
            "id": "custom_4",
            "slot": 4,
            "name": "Custom Theme 4 · Botanical Sage",
            "persona": "Your Personal Custom Slot 4",
            "mode": "light",
            "bg_base": "#f2f6f3",
            "bg_panel": "#e5ede7",
            "bg_card": "#ffffff",
            "text_primary": "#16241b",
            "text_secondary": "#475e4f",
            "accent": "#1b6b4a",
            "secondary_accent": "#9a5b13",
            "border": "#c8d8cc",
            "font_pair": "hyperlegible_clarity",
            "radius_style": "rounded",
            "texture": "ruled",
            "line_height": 1.56,
        },
    ]


def load_custom_themes() -> List[Dict[str, Any]]:
    with LIBRARY_LOCK:
        if CUSTOM_THEMES_FILE.exists():
            try:
                data = json.loads(CUSTOM_THEMES_FILE.read_text(encoding="utf-8"))
                if isinstance(data, list) and len(data) == 4:
                    return data
            except Exception:
                pass
        defaults = _default_custom_themes()
        CUSTOM_THEMES_FILE.write_text(json.dumps(defaults, indent=2, ensure_ascii=False), encoding="utf-8")
        return defaults


def save_custom_themes(themes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    defaults = _default_custom_themes()
    merged: List[Dict[str, Any]] = []
    by_id = {str(t.get("id")): t for t in (themes or []) if isinstance(t, dict)}
    for idx, d in enumerate(defaults):
        cid = f"custom_{idx + 1}"
        incoming = by_id.get(cid, {})
        merged.append({**d, **incoming, "id": cid, "slot": idx + 1})
    with LIBRARY_LOCK:
        CUSTOM_THEMES_FILE.write_text(json.dumps(merged, indent=2, ensure_ascii=False), encoding="utf-8")
    return merged


def _load_projects_index_unlocked() -> List[str]:
    if not PROJECTS_INDEX_FILE.exists():
        return []
    try:
        arr = json.loads(PROJECTS_INDEX_FILE.read_text(encoding="utf-8"))
        if isinstance(arr, list):
            return [str(x) for x in arr]
    except Exception:
        pass
    return []


def _save_projects_index_unlocked(ids: List[str]) -> None:
    deduped = list(dict.fromkeys(ids))
    PROJECTS_INDEX_FILE.write_text(json.dumps(deduped, indent=2, ensure_ascii=False), encoding="utf-8")


def _load_project_unlocked(project_id: str) -> Optional[Dict[str, Any]]:
    pf = _project_file(project_id)
    if not pf.exists():
        return None
    try:
        return json.loads(pf.read_text(encoding="utf-8"))
    except Exception:
        return None


def _save_project_unlocked(proj: Dict[str, Any]) -> None:
    pid = proj["project_id"]
    proj["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    proj["updated_ts"] = int(time.time())
    _project_file(pid).write_text(json.dumps(proj, indent=2, ensure_ascii=False), encoding="utf-8")
    idx = _load_projects_index_unlocked()
    if pid not in idx:
        idx.insert(0, pid)
        _save_projects_index_unlocked(idx)


# ==============================================================================
# v3.3 PER-DOCUMENT `.JSON` AUTO-SAVE & CONFIGURABLE 100-CHECKPOINT HISTORY
# (Hyphen-only directory & file naming preserves 100% workspace audit compliance)
# ==============================================================================
def _sanitize_hyphen_token(raw_val: str, fallback: str = "doc", max_len: int = 38) -> str:
    base = re.sub(r"\.[A-Za-z0-9]{1,6}$", "", str(raw_val or "").strip())
    slug = re.sub(r"[^a-zA-Z0-9-]+", "-", base.lower()).strip("-")
    slug = re.sub(r"-{2,}", "-", slug)[:max_len].strip("-")
    return slug or fallback


def _atomic_write_json(path_obj: Path, data: Any) -> None:
    path_obj.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path_obj.parent / f".tmp-{uuid.uuid4().hex[:8]}.json"
    tmp_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp_path.replace(path_obj)


def _load_autosave_settings_unlocked() -> Dict[str, Any]:
    max_b = DEFAULT_MAX_AUTOSAVE_BACKUPS
    updated_at = ""
    if AUTOSAVE_SETTINGS_FILE.exists():
        try:
            raw = json.loads(AUTOSAVE_SETTINGS_FILE.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                max_b = max(1, min(1000, int(raw.get("max_backups", DEFAULT_MAX_AUTOSAVE_BACKUPS))))
                updated_at = str(raw.get("updated_at") or "")
        except Exception:
            max_b = DEFAULT_MAX_AUTOSAVE_BACKUPS
    else:
        default_cfg = {"max_backups": DEFAULT_MAX_AUTOSAVE_BACKUPS, "updated_at": time.strftime("%Y-%m-%d %H:%M:%S")}
        _atomic_write_json(AUTOSAVE_SETTINGS_FILE, default_cfg)
        updated_at = default_cfg["updated_at"]
    return {
        "max_backups": max_b,
        "default_max_backups": DEFAULT_MAX_AUTOSAVE_BACKUPS,
        "updated_at": updated_at,
    }


def _compute_autosave_storage_stats_unlocked() -> Dict[str, Any]:
    PROGRESS_SAVES_DIR.mkdir(parents=True, exist_ok=True)
    doc_folders = 0
    backup_files = 0
    latest_files = 0
    total_bytes = 0
    for entry in PROGRESS_SAVES_DIR.iterdir():
        if not entry.is_dir() or entry.name.startswith("."):
            continue
        doc_folders += 1
        for f in entry.iterdir():
            if not f.is_file() or f.name.startswith("."):
                continue
            try:
                sz = f.stat().st_size
            except Exception:
                sz = 0
            total_bytes += sz
            if f.name == "latest-progress.json":
                latest_files += 1
            elif f.name.startswith("save-") and f.suffix == ".json":
                backup_files += 1
    return {
        "progress_saves_dir": str(PROGRESS_SAVES_DIR),
        "total_doc_folders": doc_folders,
        "total_backup_files": backup_files,
        "total_latest_files": latest_files,
        "total_bytes": total_bytes,
    }


def get_autosave_settings_and_stats() -> Dict[str, Any]:
    with LIBRARY_LOCK:
        cfg = _load_autosave_settings_unlocked()
        stats = _compute_autosave_storage_stats_unlocked()
    return {"ok": True, **cfg, **stats}


def _resolve_doc_autosave_key(
    project_id: Optional[str] = None,
    doc_id: Optional[str] = None,
    sha256_str: Optional[str] = None,
) -> str:
    if project_id and str(project_id).strip():
        return _sanitize_hyphen_token(str(project_id).strip(), fallback="project", max_len=28)
    if doc_id and str(doc_id).strip():
        return _sanitize_hyphen_token(str(doc_id).strip(), fallback="doc", max_len=24)
    if sha256_str and str(sha256_str).strip():
        return _sanitize_hyphen_token(str(sha256_str).strip()[:14], fallback="sha", max_len=20)
    return "plaintext-studio"


def _find_or_create_doc_autosave_dir_unlocked(
    target_key: str,
    title_hint: str = "",
    create: bool = True,
) -> Optional[Path]:
    PROGRESS_SAVES_DIR.mkdir(parents=True, exist_ok=True)
    safe_key = _sanitize_hyphen_token(target_key, fallback="doc", max_len=28)
    suffix = f"-{safe_key}"
    for existing in sorted(PROGRESS_SAVES_DIR.iterdir()):
        if existing.is_dir() and (existing.name.endswith(suffix) or existing.name == f"doc-{safe_key}"):
            return existing
    if not create:
        return None
    slug = _sanitize_hyphen_token(title_hint, fallback="document", max_len=34)
    if slug == safe_key or slug.endswith(suffix):
        folder_name = f"doc-{slug}"
    else:
        folder_name = f"doc-{slug}-{safe_key}"
    doc_dir = PROGRESS_SAVES_DIR / folder_name
    doc_dir.mkdir(parents=True, exist_ok=True)
    return doc_dir


def _extract_checkpoint_summary_from_payload(
    payload: Dict[str, Any],
    checkpoint_id: str,
    filename: str,
    filepath: str,
    size_bytes: int = 0,
    trigger_override: Optional[str] = None,
) -> Dict[str, Any]:
    rc = payload.get("resume_cursor") if isinstance(payload.get("resume_cursor"), dict) else {}
    doc_meta = payload.get("document") if isinstance(payload.get("document"), dict) else {}
    saved_iso = str(payload.get("saved_at") or payload.get("exported_at") or rc.get("updated_at") or time.strftime("%Y-%m-%dT%H:%M:%SZ"))
    saved_ts_ms = int(payload.get("saved_ts_ms") or (time.time() * 1000))
    page_num = int(rc.get("page_number") or payload.get("current_page") or 1)
    chap_num = rc.get("chapter_number") if rc.get("chapter_number") is not None else payload.get("current_chapter")
    unit_idx = int(rc.get("unit_index") or 0)
    sent_idx = int(rc.get("sentence_index") or 0)
    word_idx = int(rc.get("word_index") or 0)
    word_txt = str(rc.get("word_text") or "")
    sent_txt = re.sub(r"\s+", " ", str(rc.get("sentence_text") or "")).strip()
    if len(sent_txt) > 220:
        sent_txt = sent_txt[:220] + "…"
    return {
        "checkpoint_id": checkpoint_id,
        "filename": filename,
        "filepath": filepath,
        "size_bytes": int(size_bytes or 0),
        "trigger": str(trigger_override or payload.get("autosave_trigger") or "auto"),
        "saved_at": saved_iso,
        "saved_ts_ms": saved_ts_ms,
        "project_id": payload.get("project_id"),
        "version_id": payload.get("version_id") or rc.get("version_id"),
        "version_label": str(payload.get("version_label") or "v1.0"),
        "doc_filename": str(doc_meta.get("filename") or ""),
        "doc_filepath": str(doc_meta.get("filepath") or ""),
        "page_number": page_num,
        "chapter_number": chap_num,
        "unit_index": unit_idx,
        "sentence_index": sent_idx,
        "word_index": word_idx,
        "word_text": word_txt,
        "sentence_text": sent_txt,
        "audio_time_sec": rc.get("audio_time_sec"),
        "voice": str(payload.get("voice") or DEFAULT_VOICE),
        "speed": float(payload.get("speed") or 1.0),
        "theme_id": str(payload.get("theme_id") or "system_auto"),
        "chunk_mode": str(payload.get("chunk_mode") or "paragraph"),
        "resume_granularity": str(payload.get("resume_granularity") or "sentence"),
        "read_progress_pct": int(payload.get("read_progress_pct") or 0),
    }


def _load_or_rebuild_checkpoints_index_unlocked(doc_dir: Path) -> List[Dict[str, Any]]:
    idx_file = doc_dir / "checkpoints-index.json"
    disk_files = {
        f.name: f
        for f in doc_dir.iterdir()
        if f.is_file() and f.name.startswith("save-") and f.suffix == ".json"
    }
    loaded: List[Dict[str, Any]] = []
    if idx_file.exists():
        try:
            raw = json.loads(idx_file.read_text(encoding="utf-8"))
            if isinstance(raw, list):
                loaded = [x for x in raw if isinstance(x, dict) and x.get("filename") in disk_files]
        except Exception:
            loaded = []

    indexed_names = {x["filename"] for x in loaded}
    missing_names = [fn for fn in disk_files if fn not in indexed_names]
    if missing_names:
        for fn in missing_names:
            f_obj = disk_files[fn]
            try:
                pl = json.loads(f_obj.read_text(encoding="utf-8"))
                cid = fn[:-5]
                loaded.append(
                    _extract_checkpoint_summary_from_payload(
                        pl,
                        checkpoint_id=cid,
                        filename=fn,
                        filepath=str(f_obj),
                        size_bytes=f_obj.stat().st_size,
                    )
                )
            except Exception:
                continue
        loaded.sort(key=lambda x: (int(x.get("saved_ts_ms") or 0), str(x.get("filename") or "")), reverse=True)
        _atomic_write_json(idx_file, loaded)
    return loaded


def _prune_doc_checkpoints_unlocked(doc_dir: Path, max_backups: int) -> List[Dict[str, Any]]:
    entries = _load_or_rebuild_checkpoints_index_unlocked(doc_dir)
    entries.sort(key=lambda x: (int(x.get("saved_ts_ms") or 0), str(x.get("filename") or "")), reverse=True)
    kept = entries[: max(1, int(max_backups))]
    kept_names = {x["filename"] for x in kept}
    for f in list(doc_dir.iterdir()):
        if f.is_file() and f.name.startswith("save-") and f.suffix == ".json" and f.name not in kept_names:
            f.unlink(missing_ok=True)
    if len(kept) != len(entries):
        _atomic_write_json(doc_dir / "checkpoints-index.json", kept)
    return kept


def update_autosave_max_backups(new_max: int) -> Dict[str, Any]:
    clamped = max(1, min(1000, int(new_max)))
    now_str = time.strftime("%Y-%m-%d %H:%M:%S")
    with LIBRARY_LOCK:
        _atomic_write_json(AUTOSAVE_SETTINGS_FILE, {"max_backups": clamped, "updated_at": now_str})
        PROGRESS_SAVES_DIR.mkdir(parents=True, exist_ok=True)
        for entry in PROGRESS_SAVES_DIR.iterdir():
            if entry.is_dir() and not entry.name.startswith("."):
                _prune_doc_checkpoints_unlocked(entry, clamped)
        stats = _compute_autosave_storage_stats_unlocked()
    return {
        "ok": True,
        "max_backups": clamped,
        "default_max_backups": DEFAULT_MAX_AUTOSAVE_BACKUPS,
        "updated_at": now_str,
        **stats,
    }


def record_document_progress_autosave(
    payload: Dict[str, Any],
    trigger: str = "auto",
    create_backup: bool = True,
    force_backup: bool = False,
) -> Dict[str, Any]:
    now_ms = int(time.time() * 1000)
    now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now_ms / 1000.0))
    safe_trigger = _sanitize_hyphen_token(trigger or "auto", fallback="auto", max_len=14)
    doc_meta = payload.get("document") if isinstance(payload.get("document"), dict) else {}
    rc = payload.get("resume_cursor") if isinstance(payload.get("resume_cursor"), dict) else {}

    project_id = str(payload.get("project_id") or "").strip() or None
    doc_id = str(payload.get("doc_id") or rc.get("doc_id") or doc_meta.get("doc_id") or "").strip() or None
    sha_str = str(doc_meta.get("sha256") or "").strip() or None

    enriched_payload = {
        **payload,
        "schema": "kokoro_reader_progress_v3.3",
        "autosave_trigger": safe_trigger,
        "saved_at": now_iso,
        "saved_ts_ms": now_ms,
        "exported_at": payload.get("exported_at") or now_iso,
    }

    with LIBRARY_LOCK:
        cfg = _load_autosave_settings_unlocked()
        max_backups = cfg["max_backups"]
        proj_obj = _load_project_unlocked(project_id) if project_id else None
        title_hint = (
            (proj_obj.get("title") if proj_obj else "")
            or str(payload.get("project_title") or "")
            or str(doc_meta.get("filename") or "document")
        )
        target_key = _resolve_doc_autosave_key(project_id=project_id, doc_id=doc_id, sha256_str=sha_str)
        doc_dir = _find_or_create_doc_autosave_dir_unlocked(target_key, title_hint=title_hint, create=True)
        assert doc_dir is not None

        # Sync Project JSON resume_cursor & read_progress atomically if project exists
        if proj_obj is not None:
            changed_proj = False
            if rc:
                proj_obj["resume_cursor"] = rc
                rc_ver = payload.get("version_id") or rc.get("version_id") or proj_obj.get("active_version_id")
                if rc_ver:
                    proj_obj.setdefault("resume_cursors", {})[rc_ver] = rc
                changed_proj = True
            incoming_rp = payload.get("read_progress")
            if isinstance(incoming_rp, dict) and incoming_rp:
                cur_rp = proj_obj.setdefault("read_progress", {})
                cur_rp.update(incoming_rp)
                changed_proj = True
            if changed_proj:
                if proj_obj.get("is_sample"):
                    # Keep Sample Guide pinned cleanly without overwriting user project recency order
                    _project_file(proj_obj["project_id"]).write_text(
                        json.dumps(proj_obj, indent=2, ensure_ascii=False), encoding="utf-8"
                    )
                else:
                    _save_project_unlocked(proj_obj)

        # 1. Always update `latest-progress.json`
        latest_path = doc_dir / "latest-progress.json"
        _atomic_write_json(latest_path, enriched_payload)
        latest_sz = latest_path.stat().st_size if latest_path.exists() else 0
        latest_summary = _extract_checkpoint_summary_from_payload(
            enriched_payload,
            checkpoint_id="latest",
            filename="latest-progress.json",
            filepath=str(latest_path),
            size_bytes=latest_sz,
            trigger_override=safe_trigger,
        )

        entries = _load_or_rebuild_checkpoints_index_unlocked(doc_dir)
        backup_created = False
        active_entry = latest_summary

        # 2. Decide whether to append a rolling historical checkpoint (`save-*.json`)
        should_write_backup = bool(force_backup or create_backup)
        if should_write_backup and not force_backup and entries:
            newest = entries[0]
            same_pos = (
                str(newest.get("version_id") or "") == str(latest_summary.get("version_id") or "")
                and int(newest.get("page_number") or 1) == int(latest_summary.get("page_number") or 1)
                and int(newest.get("unit_index") or 0) == int(latest_summary.get("unit_index") or 0)
                and int(newest.get("sentence_index") or 0) == int(latest_summary.get("sentence_index") or 0)
                and int(newest.get("word_index") or 0) == int(latest_summary.get("word_index") or 0)
            )
            if same_pos:
                should_write_backup = False
            elif safe_trigger in ("advance", "auto", "live"):
                elapsed_ms = now_ms - int(newest.get("saved_ts_ms") or 0)
                if elapsed_ms < 11000:
                    should_write_backup = False

        if should_write_backup:
            ts_struct = time.localtime(now_ms / 1000.0)
            ms_part = now_ms % 1000
            p_num = max(1, int(latest_summary.get("page_number") or 1))
            u_num = max(0, int(latest_summary.get("unit_index") or 0) + 1)
            s_num = max(1, int(latest_summary.get("sentence_index") or 0) + 1)
            w_num = max(1, int(latest_summary.get("word_index") or 0) + 1)
            stamp = time.strftime("%Y%m%d-%H%M%S", ts_struct)
            cid = f"save-{stamp}-{ms_part:03d}-p{p_num:03d}-u{u_num:03d}-s{s_num:02d}-w{w_num:03d}-{safe_trigger}"
            fname = f"{cid}.json"
            b_path = doc_dir / fname
            _atomic_write_json(b_path, enriched_payload)
            b_sz = b_path.stat().st_size if b_path.exists() else 0
            new_entry = _extract_checkpoint_summary_from_payload(
                enriched_payload,
                checkpoint_id=cid,
                filename=fname,
                filepath=str(b_path),
                size_bytes=b_sz,
                trigger_override=safe_trigger,
            )
            entries.insert(0, new_entry)
            _atomic_write_json(doc_dir / "checkpoints-index.json", entries)
            entries = _prune_doc_checkpoints_unlocked(doc_dir, max_backups)
            backup_created = True
            active_entry = new_entry

        return {
            "ok": True,
            "target_key": target_key,
            "doc_saves_dir": str(doc_dir),
            "latest_filepath": str(latest_path),
            "saved_latest": True,
            "backup_created": backup_created,
            "checkpoint": active_entry,
            "total_backups_for_doc": len(entries),
            "max_backups": max_backups,
        }


def get_autosave_summary_for_project_unlocked(proj: Dict[str, Any]) -> Dict[str, Any]:
    pid = proj.get("project_id", "")
    target_key = _resolve_doc_autosave_key(project_id=pid)
    doc_dir = _find_or_create_doc_autosave_dir_unlocked(target_key, title_hint=proj.get("title", ""), create=False)
    latest_summary = None
    count = 0
    has_latest = False
    dir_str = str(PROGRESS_SAVES_DIR / f"doc-{_sanitize_hyphen_token(proj.get('title', 'document'))}-{target_key}")

    if doc_dir and doc_dir.exists():
        dir_str = str(doc_dir)
        entries = _load_or_rebuild_checkpoints_index_unlocked(doc_dir)
        count = len(entries)
        lp = doc_dir / "latest-progress.json"
        if lp.exists():
            has_latest = True
            try:
                pl = json.loads(lp.read_text(encoding="utf-8"))
                latest_summary = _extract_checkpoint_summary_from_payload(
                    pl,
                    checkpoint_id="latest",
                    filename="latest-progress.json",
                    filepath=str(lp),
                    size_bytes=lp.stat().st_size,
                )
            except Exception:
                latest_summary = entries[0] if entries else None
        elif entries:
            latest_summary = entries[0]

    # Fallback to project's inline `resume_cursor` if no `.json` file was written yet
    if latest_summary is None and isinstance(proj.get("resume_cursor"), dict):
        rc = proj["resume_cursor"]
        if rc.get("page_number"):
            latest_summary = {
                "checkpoint_id": "inline",
                "filename": "",
                "filepath": "",
                "trigger": "session",
                "saved_at": str(rc.get("updated_at") or proj.get("updated_at") or ""),
                "version_id": rc.get("version_id") or proj.get("active_version_id"),
                "version_label": "",
                "page_number": int(rc.get("page_number") or 1),
                "chapter_number": rc.get("chapter_number"),
                "unit_index": int(rc.get("unit_index") or 0),
                "sentence_index": int(rc.get("sentence_index") or 0),
                "word_index": int(rc.get("word_index") or 0),
                "word_text": str(rc.get("word_text") or ""),
                "sentence_text": str(rc.get("sentence_text") or "")[:200],
            }

    return {
        "autosave_count": count,
        "has_latest_autosave": has_latest,
        "autosave_dir": dir_str,
        "latest_autosave": latest_summary,
    }


def list_document_checkpoints(
    project_id: Optional[str] = None,
    doc_id: Optional[str] = None,
    version_id: Optional[str] = None,
) -> Dict[str, Any]:
    with LIBRARY_LOCK:
        cfg = _load_autosave_settings_unlocked()
        proj_obj = _load_project_unlocked(project_id) if project_id else None
        title_hint = (proj_obj.get("title") if proj_obj else "") or "document"
        target_key = _resolve_doc_autosave_key(project_id=project_id, doc_id=doc_id)
        doc_dir = _find_or_create_doc_autosave_dir_unlocked(target_key, title_hint=title_hint, create=True)
        assert doc_dir is not None
        entries = _load_or_rebuild_checkpoints_index_unlocked(doc_dir)
        if version_id and version_id.strip():
            entries = [e for e in entries if not e.get("version_id") or e.get("version_id") == version_id.strip()]
        latest_summary = None
        lp = doc_dir / "latest-progress.json"
        if lp.exists():
            try:
                pl = json.loads(lp.read_text(encoding="utf-8"))
                latest_summary = _extract_checkpoint_summary_from_payload(
                    pl,
                    checkpoint_id="latest",
                    filename="latest-progress.json",
                    filepath=str(lp),
                    size_bytes=lp.stat().st_size,
                )
            except Exception:
                latest_summary = None
    return {
        "ok": True,
        "target_key": target_key,
        "project_id": project_id,
        "project_title": proj_obj.get("title") if proj_obj else None,
        "doc_saves_dir": str(doc_dir),
        "progress_saves_root": str(PROGRESS_SAVES_DIR),
        "latest_filepath": str(doc_dir / "latest-progress.json"),
        "max_backups": cfg["max_backups"],
        "count": len(entries),
        "latest": latest_summary,
        "checkpoints": entries,
    }


def _load_project_comments_unlocked(project_id: str) -> List[Dict[str, Any]]:
    cf = _comments_file(project_id)
    if not cf.exists():
        return []
    try:
        arr = json.loads(cf.read_text(encoding="utf-8"))
        if isinstance(arr, list):
            return arr
    except Exception:
        pass
    return []


def _save_project_comments_unlocked(project_id: str, comments: List[Dict[str, Any]]) -> None:
    _comments_file(project_id).write_text(json.dumps(comments, indent=2, ensure_ascii=False), encoding="utf-8")


def compute_comments_overlap_metadata(comments: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Solves overlapping character-span comments & suggestions on the same (version_id, page_number, unit_index).
    - Groups overlapping intervals using sweep-line connected components (`overlap_group_id`).
    - Assigns `overlap_count` and `layer_index` (narrowest span = top priority layer 0).
    - Flags `suggestion_conflict = True` whenever >= 2 OPEN suggestions overlap on the same text span!
    """
    by_unit: Dict[str, List[Dict[str, Any]]] = {}
    for c in comments:
        c["overlap_group_id"] = None
        c["overlap_count"] = 1
        c["layer_index"] = 0
        c["suggestion_conflict"] = False
        c["conflicting_comment_ids"] = []
        if (
            c.get("status") not in ("archived",)
            and c.get("char_start") is not None
            and c.get("char_end") is not None
            and int(c["char_end"]) > int(c["char_start"])
            and c.get("page_number") is not None
            and c.get("unit_index") is not None
        ):
            uk = f"{c.get('version_id','')}:p{c['page_number']}:u{c['unit_index']}"
            by_unit.setdefault(uk, []).append(c)

    conflict_groups: List[Dict[str, Any]] = []

    for uk, unit_comments in by_unit.items():
        if len(unit_comments) < 2:
            continue
        sorted_c = sorted(unit_comments, key=lambda x: (int(x["char_start"]), int(x["char_end"])))
        clusters: List[List[Dict[str, Any]]] = []
        cur_cluster = [sorted_c[0]]
        cur_max_end = int(sorted_c[0]["char_end"])

        for nxt in sorted_c[1:]:
            s = int(nxt["char_start"])
            e = int(nxt["char_end"])
            if s < cur_max_end:
                cur_cluster.append(nxt)
                cur_max_end = max(cur_max_end, e)
            else:
                clusters.append(cur_cluster)
                cur_cluster = [nxt]
                cur_max_end = e
        clusters.append(cur_cluster)

        for cl_idx, cluster in enumerate(clusters):
            if len(cluster) < 2:
                continue
            gid = f"ovl-{hashlib.sha1(f'{uk}:{cl_idx}'.encode()).hexdigest()[:8]}"
            # Sort from narrowest span to widest span so word < phrase < sentence
            by_span_len = sorted(cluster, key=lambda x: (int(x["char_end"]) - int(x["char_start"]), x.get("created_ts", 0)))
            open_suggestions = [
                x for x in cluster
                if x.get("kind") == "suggestion" and x.get("status", "open") == "open"
            ]
            # Check pairwise overlap specifically among open suggestions
            conflicting_ids: set[str] = set()
            for i in range(len(open_suggestions)):
                for j in range(i + 1, len(open_suggestions)):
                    a = open_suggestions[i]
                    b = open_suggestions[j]
                    if max(int(a["char_start"]), int(b["char_start"])) < min(int(a["char_end"]), int(b["char_end"])):
                        conflicting_ids.add(a["comment_id"])
                        conflicting_ids.add(b["comment_id"])

            for layer_i, item in enumerate(by_span_len):
                item["overlap_group_id"] = gid
                item["overlap_count"] = len(cluster)
                item["layer_index"] = layer_i
                if item["comment_id"] in conflicting_ids:
                    item["suggestion_conflict"] = True
                    item["conflicting_comment_ids"] = sorted(cid for cid in conflicting_ids if cid != item["comment_id"])

            if conflicting_ids:
                conflict_groups.append({
                    "overlap_group_id": gid,
                    "unit_key": uk,
                    "page_number": cluster[0].get("page_number"),
                    "unit_index": cluster[0].get("unit_index"),
                    "comment_ids": sorted(conflicting_ids),
                    "count": len(conflicting_ids),
                })

    return comments, conflict_groups


SAMPLE_GUIDE_FILENAME = "KOKORO_ASSET_SampleGuide_v1.0_20261021_FINAL.pdf"


def _is_sample_guide_doc(filename: str = "", filepath: str = "") -> bool:
    return SAMPLE_GUIDE_FILENAME in (filename or "") or SAMPLE_GUIDE_FILENAME in (filepath or "")


def ensure_project_for_document(doc_record: Dict[str, Any]) -> Dict[str, Any]:
    """
    Finds an existing Project Version matching `sha256` or `filepath` (or creates a new Project `v1.0`)
    so every document has persistent theme storage, voice cast assignments, and versioned comment history.
    Note: Built-in Sample Guide PDF only binds to dedicated Sample Guide project(s), NEVER hijacking user projects.
    """
    doc_sha = doc_record.get("sha256", "")
    doc_path = doc_record.get("filepath", "")
    doc_fname = doc_record.get("filename", "")
    doc_id = doc_record["doc_id"]
    is_sample_doc = _is_sample_guide_doc(doc_fname, doc_path)
    full_txt = _extract_full_text_from_doc(doc_record)

    with LIBRARY_LOCK:
        index_ids = _load_projects_index_unlocked()
        for pid in index_ids:
            proj = _load_project_unlocked(pid)
            if not proj:
                continue
            proj_is_sample = bool(proj.get("is_sample")) or ("sample guide" in (proj.get("title") or "").lower())
            if is_sample_doc and not proj_is_sample:
                continue
            if not is_sample_doc and proj_is_sample:
                continue
            for ver in proj.get("versions") or []:
                if (doc_sha and ver.get("sha256") == doc_sha) or (
                    doc_path and not doc_path.startswith("library://") and ver.get("filepath") == doc_path and ver.get("sha256") == doc_sha
                ):
                    ver["doc_id"] = doc_id
                    ver["filename"] = doc_fname or ver.get("filename", "Document")
                    ver["doc_type"] = doc_record.get("doc_type", ver.get("doc_type", "Document"))
                    ver["total_pages"] = doc_record.get("total_pages", ver.get("total_pages", 1))
                    ver["total_chapters"] = doc_record.get("total_chapters", ver.get("total_chapters", 0))
                    ver["total_paragraphs"] = doc_record.get("total_paragraphs", ver.get("total_paragraphs", 0))
                    ver["total_words"] = doc_record.get("total_words", ver.get("total_words", 0))
                    proj["active_version_id"] = ver["version_id"]
                    snap_p = _snapshot_file(ver["version_id"])
                    if not snap_p.exists() or snap_p.read_text(encoding="utf-8", errors="ignore").lstrip().startswith("{\\rtf"):
                        snap_p.write_text(full_txt, encoding="utf-8")
                    _save_project_unlocked(proj)
                    return {"project": proj, "active_version": ver}

        # Create a fresh v1.0 project
        pid = uuid.uuid4().hex[:12]
        vid = uuid.uuid4().hex[:12]
        now_str = time.strftime("%Y-%m-%d %H:%M:%S")
        now_ts = int(time.time())
        _snapshot_file(vid).write_text(full_txt, encoding="utf-8")

        ver_obj = {
            "version_id": vid,
            "version_label": "v1.0",
            "major": 1,
            "minor": 0,
            "doc_id": doc_id,
            "filename": doc_record.get("filename", "Document"),
            "filepath": doc_path,
            "extension": doc_record.get("extension", ".txt"),
            "doc_type": doc_record.get("doc_type", "Document"),
            "sha256": doc_sha,
            "size_bytes": doc_record.get("size_bytes", 0),
            "total_pages": doc_record.get("total_pages", 1),
            "total_chapters": doc_record.get("total_chapters", 0),
            "total_paragraphs": doc_record.get("total_paragraphs", 0),
            "total_words": doc_record.get("total_words", 0),
            "notes": "Initial v1.0 baseline imported into Kokoro Studio Library",
            "parent_version_id": None,
            "source_kind": "file",
            "created_at": now_str,
            "created_ts": now_ts,
        }
        proj_obj = {
            "project_id": pid,
            "title": "Kokoro-82M Sample Guide" if is_sample_doc else _clean_project_title(doc_record.get("filename", "Untitled Document")),
            "description": f"{doc_record.get('doc_type', 'Document')} · {doc_record.get('total_pages', 1)} pages",
            "tags": ["sample", "pdf"] if is_sample_doc else [doc_record.get("extension", ".pdf").lstrip(".") or "doc"],
            "is_sample": is_sample_doc,
            "theme_id": "system_auto",
            "default_voice": DEFAULT_VOICE,
            "voice_assignments": {"chapters": {}, "units": {}},
            "read_progress": {},
            "active_version_id": vid,
            "versions": [ver_obj],
            "created_at": now_str,
            "created_ts": now_ts,
            "updated_at": now_str,
            "updated_ts": now_ts,
        }
        _save_project_unlocked(proj_obj)
        return {"project": proj_obj, "active_version": ver_obj}


def activate_project_version_into_memory(proj: Dict[str, Any], ver: Dict[str, Any]) -> Dict[str, Any]:
    doc_id = ver.get("doc_id")
    if doc_id and doc_id in DOCUMENTS_STORE:
        doc = DOCUMENTS_STORE[doc_id]
    else:
        fp = ver.get("filepath") or ""
        clean_ver_fname = re.sub(r"^\d{9,12}_", "", ver.get("filename") or "")
        if fp and not fp.startswith("library://") and not fp.startswith("text://") and Path(fp).exists():
            doc = load_document_from_path(fp, auto_link_project=False, display_filename=clean_ver_fname or None)
            ver["doc_id"] = doc["doc_id"]
            ver["filename"] = doc["filename"]
            ver["doc_type"] = doc["doc_type"]
            ver["total_pages"] = doc["total_pages"]
            ver["total_chapters"] = doc["total_chapters"]
            ver["total_paragraphs"] = doc["total_paragraphs"]
            ver["total_words"] = doc["total_words"]
            _snapshot_file(ver["version_id"]).write_text(_extract_full_text_from_doc(doc), encoding="utf-8")
        else:
            snap_p = _snapshot_file(ver["version_id"])
            raw_txt = snap_p.read_text(encoding="utf-8") if snap_p.exists() else ""
            if raw_txt.lstrip().startswith("{\\rtf"):
                raw_txt = convert_rtf_to_plain_text(raw_rtf=raw_txt)
                if snap_p.exists():
                    snap_p.write_text(raw_txt, encoding="utf-8")
            doc = build_doc_record_from_text(
                raw_txt,
                filename=clean_ver_fname or f"{proj['title']}_{ver['version_label']}.md",
                filepath_override=fp or f"library://{proj['project_id']}/{ver['version_label']}",
                doc_id_override=doc_id,
            )
            ver["doc_id"] = doc["doc_id"]
            ver["filename"] = doc["filename"]
            ver["total_pages"] = doc["total_pages"]
            ver["total_chapters"] = doc["total_chapters"]
            ver["total_paragraphs"] = doc["total_paragraphs"]
            ver["total_words"] = doc["total_words"]

    doc["project_id"] = proj["project_id"]
    doc["version_id"] = ver["version_id"]
    doc["version_label"] = ver["version_label"]
    doc["project_theme_id"] = proj.get("theme_id", "system_auto")
    return doc


def repair_existing_library_projects_on_boot() -> None:
    """
    Non-destructively heals existing Library project records on startup:
    1. Detaches accidental SampleGuide.pdf links from user-titled projects (e.g., 'Collected Essays')
       so opening Sample Guide never hijacks user projects.
    2. Converts any raw `.rtf` snapshots (`{\\rtf1...`) into clean Unicode prose & updates page/paragraph counts.
    3. Cleans ugly `1794474660_` upload prefixes and raw `PROJECT DOC ...` auto-generated titles while preserving
       100% of user read_progress, themes, voice casts, and comments.
    """
    with LIBRARY_LOCK:
        pids = _load_projects_index_unlocked()

    for pid in pids:
        with LIBRARY_LOCK:
            proj = _load_project_unlocked(pid)
        if not proj:
            continue

        changed = False
        orig_updated_at = proj.get("updated_at")
        orig_updated_ts = proj.get("updated_ts")
        title_str = (proj.get("title") or "").strip()
        is_sample_proj = bool(proj.get("is_sample")) or ("sample guide" in title_str.lower())
        if is_sample_proj and not proj.get("is_sample"):
            proj["is_sample"] = True
            changed = True

        # Clean raw auto-generated workspace title like "PROJECT DOC SampleManuscript v1.0 20260101 FINAL"
        if re.match(r"^[A-Z0-9]+\s+[A-Z]+\s+[A-Za-z0-9]+\s+v\d+", title_str):
            vers = proj.get("versions") or []
            src_fname = re.sub(r"^\d{9,12}_", "", (vers[-1].get("filename") or "")) if vers else ""
            cleaned_t = _clean_project_title(src_fname or title_str)
            if cleaned_t and cleaned_t != title_str:
                proj["title"] = cleaned_t
                changed = True

        for ver in proj.get("versions") or []:
            v_fname = ver.get("filename") or ""
            v_fpath = ver.get("filepath") or ""

            # 1) Detach accidental SampleGuide attachment on user-created projects
            if not is_sample_proj and _is_sample_guide_doc(v_fname, v_fpath) and ver.get("source_kind") == "create":
                placeholder_md = (
                    f"# {title_str or 'Untitled Project'}\n\n"
                    f"This project (`{ver.get('version_label', 'v1.0')}`) is ready for your manuscript. "
                    f"Click **➕ Add Version** on the Library card (or **✏️ Edit** in the Reader) to upload a PDF, Word, RTF, Markdown, or text file."
                )
                clean_doc = build_doc_record_from_text(
                    placeholder_md,
                    filename=f"{re.sub(r'[^A-Za-z0-9_-]+', '_', title_str or 'Project')[:40]}_v1.0.md",
                    filepath_override=f"library://{pid}/{ver.get('version_label', 'v1.0')}",
                )
                ver["doc_id"] = clean_doc["doc_id"]
                ver["filename"] = clean_doc["filename"]
                ver["filepath"] = clean_doc["filepath"]
                ver["extension"] = ".md"
                ver["doc_type"] = "Markdown Document (.md)"
                ver["sha256"] = clean_doc["sha256"]
                ver["size_bytes"] = clean_doc["size_bytes"]
                ver["total_pages"] = clean_doc["total_pages"]
                ver["total_chapters"] = clean_doc["total_chapters"]
                ver["total_paragraphs"] = clean_doc["total_paragraphs"]
                ver["total_words"] = clean_doc["total_words"]
                _snapshot_file(ver["version_id"]).write_text(_extract_full_text_from_doc(clean_doc), encoding="utf-8")
                changed = True
                continue

            # 2) Strip timestamp prefix from stored display filename
            stripped_fname = re.sub(r"^\d{9,12}_", "", v_fname)
            if stripped_fname and stripped_fname != v_fname:
                ver["filename"] = stripped_fname
                changed = True

            # 3) Repair raw RTF snapshots or re-sync divider-cleaned paragraph counts from disk
            snap_p = _snapshot_file(ver["version_id"])
            snap_raw = snap_p.read_text(encoding="utf-8", errors="ignore") if snap_p.exists() else ""
            needs_reparse = snap_raw.lstrip().startswith("{\\rtf") or (ver.get("extension") == ".md" and "\n---\n" in snap_raw)
            if needs_reparse and v_fpath and not v_fpath.startswith("library://") and Path(v_fpath).exists():
                try:
                    re_doc = load_document_from_path(v_fpath, auto_link_project=False, display_filename=ver["filename"])
                    ver["doc_id"] = re_doc["doc_id"]
                    ver["doc_type"] = re_doc["doc_type"]
                    ver["total_pages"] = re_doc["total_pages"]
                    ver["total_chapters"] = re_doc["total_chapters"]
                    ver["total_paragraphs"] = re_doc["total_paragraphs"]
                    ver["total_words"] = re_doc["total_words"]
                    snap_p.write_text(_extract_full_text_from_doc(re_doc), encoding="utf-8")
                    proj["description"] = f"{re_doc['doc_type']} · {re_doc['total_pages']} pages"
                    changed = True
                except Exception as exc:
                    print(f"[Library Repair] Warning re-parsing {v_fpath}: {exc}")

        if changed:
            with LIBRARY_LOCK:
                pf = _project_file(pid)
                if orig_updated_at:
                    proj["updated_at"] = orig_updated_at
                if orig_updated_ts:
                    proj["updated_ts"] = orig_updated_ts
                pf.write_text(json.dumps(proj, indent=2, ensure_ascii=False), encoding="utf-8")


def ensure_seed_library_showcase() -> None:
    """
    Seeds a multi-version editorial showcase project (`v1.0` -> `v1.1`) on first boot
    so users immediately see how multi-version projects, cross-version comment history,
    and organic search work alongside their own documents.
    """
    seed_pid = "seed-monograph-v3"
    with LIBRARY_LOCK:
        if _project_file(seed_pid).exists():
            return

    v1_text = (
        "# Chapter 1: The Craft of Deep Listening\n\n"
        "Reading with the ear reveals structural cadence that silent skimming routinely overlooks. "
        "When a paragraph breathes naturally, listeners absorb complex arguments without cognitive fatigue.\n\n"
        "In early speech tools, rigid line-by-line synthesis fractured every sentence at arbitrary visual margins.\n\n"
        "# Chapter 2: Editorial Annotations in Motion\n\n"
        "Capturing a fleeting thought while listening requires frictionless keyboard ergonomics. "
        "Pressing C for a comment or S for a wording suggestion lets an editor annotate a manuscript mid-flight."
    )
    v2_text = (
        "# Chapter 1: The Craft of Deep Listening\n\n"
        "Listening to prose aloud reveals rhythmic cadence and tonal friction that silent skimming routinely misses. "
        "When each paragraph is synthesized first and streamed seamlessly, listeners begin in under a second while remaining passages cache quietly in the background.\n\n"
        "Unlike legacy readers that paused to render entire chapters upfront, paragraph-first streaming keeps creative momentum unbroken.\n\n"
        "# Chapter 2: Editorial Annotations & Multi-Voice Casts\n\n"
        "Capturing an editorial insight while listening demands instantaneous, non-overlapping shortcuts. "
        "Pressing C for a comment or S for a suggestion anchors feedback directly to the spoken word, paragraph, chapter, or page—ready to export to an LLM for versioned revision."
    )

    doc_v1 = build_doc_record_from_text(v1_text, filename="Architecture_of_Deep_Listening_v1.0.md")
    doc_v2 = build_doc_record_from_text(v2_text, filename="Architecture_of_Deep_Listening_v1.1.md")

    vid1 = "seedver10"
    vid2 = "seedver11"
    now_str = time.strftime("%Y-%m-%d %H:%M:%S")
    now_ts = int(time.time())

    with LIBRARY_LOCK:
        _snapshot_file(vid1).write_text(v1_text, encoding="utf-8")
        _snapshot_file(vid2).write_text(v2_text, encoding="utf-8")

        proj = {
            "project_id": seed_pid,
            "title": "The Architecture of Deep Listening — Editorial Monograph",
            "description": "Multi-version editorial showcase with cross-version comment history, voice cast overrides, and LLM revision lineage.",
            "tags": ["editorial", "monograph", "multi-version", "showcase"],
            "theme_id": "kyoto_washi_sumi",
            "default_voice": "af_aoede",
            "voice_assignments": {
                "chapters": {"1": "af_aoede", "2": "bf_emma"},
                "units": {},
            },
            "read_progress": {},
            "active_version_id": vid2,
            "versions": [
                {
                    "version_id": vid1,
                    "version_label": "v1.0",
                    "major": 1,
                    "minor": 0,
                    "doc_id": doc_v1["doc_id"],
                    "filename": doc_v1["filename"],
                    "filepath": doc_v1["filepath"],
                    "extension": ".md",
                    "doc_type": "Markdown Document (.md)",
                    "sha256": doc_v1["sha256"],
                    "size_bytes": doc_v1["size_bytes"],
                    "total_pages": doc_v1["total_pages"],
                    "total_chapters": doc_v1["total_chapters"],
                    "total_paragraphs": doc_v1["total_paragraphs"],
                    "total_words": doc_v1["total_words"],
                    "notes": "v1.0 Original rough manuscript draft",
                    "parent_version_id": None,
                    "source_kind": "paste",
                    "created_at": now_str,
                    "created_ts": now_ts - 3600,
                },
                {
                    "version_id": vid2,
                    "version_label": "v1.1",
                    "major": 1,
                    "minor": 1,
                    "doc_id": doc_v2["doc_id"],
                    "filename": doc_v2["filename"],
                    "filepath": doc_v2["filepath"],
                    "extension": ".md",
                    "doc_type": "Markdown Document (.md)",
                    "sha256": doc_v2["sha256"],
                    "size_bytes": doc_v2["size_bytes"],
                    "total_pages": doc_v2["total_pages"],
                    "total_chapters": doc_v2["total_chapters"],
                    "total_paragraphs": doc_v2["total_paragraphs"],
                    "total_words": doc_v2["total_words"],
                    "notes": "v1.1 LLM-revised edition incorporating v1.0 pacing & paragraph-first streaming suggestions",
                    "parent_version_id": vid1,
                    "source_kind": "llm_revision",
                    "created_at": now_str,
                    "created_ts": now_ts,
                },
            ],
            "created_at": now_str,
            "created_ts": now_ts - 3600,
            "updated_at": now_str,
            "updated_ts": now_ts,
        }
        _save_project_unlocked(proj)

        seed_comments = [
            {
                "comment_id": "seed-cmt-1",
                "project_id": seed_pid,
                "version_id": vid1,
                "version_label": "v1.0",
                "doc_id": doc_v1["doc_id"],
                "kind": "suggestion",
                "scope": "phrase",
                "page_number": 1,
                "chapter_number": 1,
                "unit_index": 1,
                "unit_mode": "paragraph",
                "char_start": 0,
                "char_end": 20,
                "selected_text": "Reading with the ear",
                "context_before": "",
                "context_after": " reveals structural cadence",
                "content": "Sharpen opening hook to emphasize spoken prose aloud.",
                "suggested_text": "Listening to prose aloud",
                "status": "applied",
                "audio_timestamp_sec": 0.4,
                "voice_at_creation": "af_aoede",
                "created_at": now_str,
                "created_ts": now_ts - 3200,
                "updated_at": now_str,
                "history": [
                    {"action": "created", "timestamp": now_str, "version_label": "v1.0", "note": "Created suggestion on v1.0"},
                    {"action": "applied", "timestamp": now_str, "version_label": "v1.1", "note": "Applied during LLM revision bump to v1.1"},
                ],
            },
            {
                "comment_id": "seed-cmt-2",
                "project_id": seed_pid,
                "version_id": vid2,
                "version_label": "v1.1",
                "doc_id": doc_v2["doc_id"],
                "kind": "comment",
                "scope": "phrase",
                "page_number": 1,
                "chapter_number": 1,
                "unit_index": 1,
                "unit_mode": "paragraph",
                "char_start": 33,
                "char_end": 71,
                "selected_text": "rhythmic cadence and tonal friction",
                "context_before": "Listening to prose aloud reveals ",
                "context_after": " that silent skimming routinely misses.",
                "content": "Love how Aoede voices the contrast between rhythmic cadence and tonal friction here.",
                "suggested_text": "",
                "status": "open",
                "audio_timestamp_sec": 2.1,
                "voice_at_creation": "af_aoede",
                "created_at": now_str,
                "created_ts": now_ts - 600,
                "updated_at": now_str,
                "history": [
                    {"action": "created", "timestamp": now_str, "version_label": "v1.1", "note": "Added listening note on v1.1"},
                ],
            },
        ]
        _save_project_comments_unlocked(seed_pid, seed_comments)


def build_project_summary_card(proj: Dict[str, Any], comments: List[Dict[str, Any]], match_info: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    versions = proj.get("versions") or []
    active_vid = proj.get("active_version_id")
    active_ver = next((v for v in versions if v["version_id"] == active_vid), versions[-1] if versions else {})
    open_comments = sum(1 for c in comments if c.get("kind") == "comment" and c.get("status") == "open")
    open_suggestions = sum(1 for c in comments if c.get("kind") == "suggestion" and c.get("status") == "open")
    resolved_or_applied = sum(1 for c in comments if c.get("status") in ("resolved", "applied"))
    _, conflicts = compute_comments_overlap_metadata([dict(c) for c in comments])

    doc_id = active_ver.get("doc_id", "")
    gen_stat = get_generated_status_for_doc(doc_id) if doc_id else {"total_generated_items": 0, "voices_used": []}

    assigned_voices = set([proj.get("default_voice", DEFAULT_VOICE)])
    for v in (proj.get("voice_assignments", {}).get("chapters") or {}).values():
        if v:
            assigned_voices.add(v)
    for v in (proj.get("voice_assignments", {}).get("units") or {}).values():
        if v:
            assigned_voices.add(v)
    for vu in gen_stat.get("voices_used") or []:
        assigned_voices.add(vu["voice_id"])

    # Compute active-version reading progress percentage
    rp_map = proj.get("read_progress") or {}
    ver_sha = active_ver.get("sha256") or ""
    ver_did = active_ver.get("doc_id") or ""
    para_read_arr = rp_map.get(f"{ver_sha}:paragraph") or rp_map.get(f"{ver_did}:paragraph") or []
    total_paras_ver = max(0, int(active_ver.get("total_paragraphs") or 0))
    read_units_cnt = min(len(set(para_read_arr)), total_paras_ver) if total_paras_ver > 0 else len(set(para_read_arr))
    read_pct = round((read_units_cnt / total_paras_ver) * 100) if total_paras_ver > 0 else 0
    is_sample_flag = bool(proj.get("is_sample")) or _is_sample_guide_doc(active_ver.get("filename", ""), active_ver.get("filepath", ""))
    with LIBRARY_LOCK:
        autosave_info = get_autosave_summary_for_project_unlocked(proj)

    return {
        "project_id": proj["project_id"],
        "title": proj.get("title", "Untitled Project"),
        "description": proj.get("description", ""),
        "tags": proj.get("tags", []),
        "is_sample": is_sample_flag,
        "theme_id": proj.get("theme_id", "system_auto"),
        "default_voice": proj.get("default_voice", DEFAULT_VOICE),
        "voices_palette": [get_voice_color_meta(v) for v in sorted(assigned_voices)],
        "version_count": len(versions),
        "latest_version_label": versions[-1]["version_label"] if versions else "v1.0",
        "active_version_id": active_ver.get("version_id"),
        "active_version_label": active_ver.get("version_label", "v1.0"),
        "active_filename": re.sub(r"^\d{9,12}_", "", active_ver.get("filename", "")),
        "total_pages": active_ver.get("total_pages", 1),
        "total_chapters": active_ver.get("total_chapters", 0),
        "total_paragraphs": total_paras_ver,
        "total_words": active_ver.get("total_words", 0),
        "read_units_count": read_units_cnt,
        "total_units_count": total_paras_ver,
        "read_progress_pct": read_pct,
        "open_comments_count": open_comments,
        "open_suggestions_count": open_suggestions,
        "resolved_count": resolved_or_applied,
        "total_annotations_count": len(comments),
        "conflict_count": len(conflicts),
        "generated_items_count": gen_stat.get("total_generated_items", 0),
        "autosave_count": autosave_info["autosave_count"],
        "has_latest_autosave": autosave_info["has_latest_autosave"],
        "autosave_dir": autosave_info["autosave_dir"],
        "latest_autosave": autosave_info["latest_autosave"],
        "updated_at": proj.get("updated_at", ""),
        "updated_ts": proj.get("updated_ts", 0),
        "versions_brief": [
            {
                "version_id": v["version_id"],
                "version_label": v["version_label"],
                "filename": v.get("filename", ""),
                "notes": v.get("notes", ""),
                "total_words": v.get("total_words", 0),
                "created_at": v.get("created_at", ""),
                "source_kind": v.get("source_kind", "file"),
            }
            for v in versions
        ],
        "search_match": match_info,
    }


def search_projects_organically(query: str = "", filter_chip: str = "all", sort_by: str = "updated") -> List[Dict[str, Any]]:
    """
    Natural, organic search across the entire Project Library:
    - Understands natural intent words ('unresolved', 'open', 'suggestions', 'comments', 'conflicts',
      'generated', 'audio', 'multi-version', 'pdf', 'markdown', voice names like 'aoede', 'emma', 'fenrir')
    - Ranks matches across Project Title, Filenames, Version Notes, Comment/Suggestion Text, and Full Document Text,
      returning contextual snippets explaining WHY each project matched.
    """
    with LIBRARY_LOCK:
        pids = _load_projects_index_unlocked()
        loaded_pairs = []
        for pid in pids:
            pr = _load_project_unlocked(pid)
            if pr:
                cmts = _load_project_comments_unlocked(pid)
                loaded_pairs.append((pr, cmts))

    q_clean = (query or "").strip()
    q_lower = q_clean.lower()
    raw_tokens = [t for t in re.split(r"\s+", q_lower) if t]

    # Natural filter chip or natural phrase detection
    require_open_suggestions = filter_chip == "suggestions" or ("unresolved" in raw_tokens and "suggestion" in q_lower)
    require_comments = filter_chip == "comments"
    require_multiversion = filter_chip == "multiversion"
    require_generated = filter_chip == "generated"
    require_conflicts = filter_chip == "conflicts" or "conflict" in q_lower or "overlapping" in q_lower
    require_in_progress = filter_chip == "in_progress"
    require_autosaves = filter_chip == "autosaves"

    stop_words = {"in", "the", "a", "an", "with", "for", "of", "on", "to", "and", "or", "show", "me", "find", "projects", "docs", "documents"}
    search_tokens = [t for t in raw_tokens if t not in stop_words]

    results: List[Tuple[float, Dict[str, Any]]] = []

    for proj, cmts in loaded_pairs:
        card = build_project_summary_card(proj, cmts)

        if require_open_suggestions and card["open_suggestions_count"] == 0:
            continue
        if require_comments and card["total_annotations_count"] == 0:
            continue
        if require_multiversion and card["version_count"] < 2:
            continue
        if require_generated and card["generated_items_count"] == 0:
            continue
        if require_conflicts and card["conflict_count"] == 0:
            continue
        if require_in_progress and card["read_units_count"] == 0 and not card.get("latest_autosave"):
            continue
        if require_autosaves and card.get("autosave_count", 0) == 0 and not card.get("has_latest_autosave"):
            continue

        if not q_clean:
            # Pin non-sample user projects ahead of built-in Sample Guide by default unless Sample Guide was just updated
            sample_penalty = -10_000_000_000.0 if card.get("is_sample") else 0.0
            results.append((sample_penalty + float(card["updated_ts"]), card))
            continue

        score = 0.0
        reasons: List[str] = []
        snippet = ""

        title_l = proj.get("title", "").lower()
        desc_l = proj.get("description", "").lower()
        tags_l = " ".join(proj.get("tags") or []).lower()
        fnames_l = " ".join(v.get("filename", "") + " " + v.get("notes", "") + " " + v.get("version_label", "") for v in (proj.get("versions") or [])).lower()
        voices_l = " ".join(vp["voice_name"] + " " + vp["voice_id"] for vp in card["voices_palette"]).lower()

        # Phrase bonus
        if q_lower in title_l:
            score += 90.0
            reasons.append("Title match")
        if q_lower in fnames_l:
            score += 55.0
            reasons.append("Filename / version note")

        # Natural intent bonuses
        if any(w in raw_tokens for w in ("suggestion", "suggestions")) and card["open_suggestions_count"] > 0:
            score += 30.0
            reasons.append(f"{card['open_suggestions_count']} open suggestion(s)")
        if any(w in raw_tokens for w in ("comment", "comments", "notes", "annotations")) and card["total_annotations_count"] > 0:
            score += 25.0
            reasons.append(f"{card['total_annotations_count']} annotation(s)")
        if any(w in raw_tokens for w in ("unresolved", "open")) and (card["open_comments_count"] + card["open_suggestions_count"]) > 0:
            score += 25.0
            reasons.append("Has open feedback")
        if any(w in raw_tokens for w in ("generated", "cached", "audio", "spoken")) and card["generated_items_count"] > 0:
            score += 25.0
            reasons.append(f"{card['generated_items_count']} generated audio unit(s)")

        # Check comments & suggestions text
        for c in cmts:
            c_blob = f"{c.get('content','')} {c.get('suggested_text','')} {c.get('selected_text','')}".lower()
            if q_lower in c_blob or (search_tokens and all(tk in c_blob for tk in search_tokens)):
                score += 48.0
                kind_lbl = "Suggestion" if c.get("kind") == "suggestion" else "Comment"
                reasons.append(f"{kind_lbl} in {c.get('version_label','v1.0')}")
                snippet = f"💬 {kind_lbl} ({c.get('version_label','v1.0')}): “{(c.get('content') or c.get('suggested_text') or '')[:120]}”"
                break

        # Check full text snapshots across versions
        body_blob = ""
        matched_ver_lbl = ""
        for v in reversed(proj.get("versions") or []):
            sp = _snapshot_file(v["version_id"])
            if sp.exists():
                try:
                    vt = sp.read_text(encoding="utf-8")
                except Exception:
                    vt = ""
                vt_l = vt.lower()
                if q_lower in vt_l:
                    pos = vt_l.find(q_lower)
                    start_s = max(0, pos - 50)
                    end_s = min(len(vt), pos + len(q_lower) + 75)
                    excerpt = re.sub(r"\s+", " ", vt[start_s:end_s]).strip()
                    score += 42.0
                    matched_ver_lbl = v["version_label"]
                    reasons.append(f"Text match in {matched_ver_lbl}")
                    if not snippet:
                        snippet = f"📄 {matched_ver_lbl}: “…{excerpt}…”"
                    body_blob = vt_l
                    break
                if not body_blob:
                    body_blob = vt_l

        # Token-level organic matching
        combined_haystack = f"{title_l} {desc_l} {tags_l} {fnames_l} {voices_l} {body_blob}"
        matched_tokens = 0
        for tk in (search_tokens or raw_tokens):
            if tk in title_l:
                score += 26.0
                matched_tokens += 1
            elif tk in voices_l:
                score += 24.0
                matched_tokens += 1
                reasons.append(f"Voice: {tk.title()}")
            elif tk in fnames_l or tk in tags_l or tk in desc_l:
                score += 18.0
                matched_tokens += 1
            elif tk in combined_haystack:
                score += 10.0
                matched_tokens += 1
            else:
                # Fuzzy prefix / typo tolerance on project title words
                for tw in re.findall(r"\w+", f"{title_l} {voices_l} {tags_l}"):
                    if len(tk) >= 4 and (tw.startswith(tk) or difflib.SequenceMatcher(None, tk, tw).ratio() >= 0.80):
                        score += 9.0
                        matched_tokens += 1
                        break

        if score > 0 and (matched_tokens > 0 or reasons):
            dedup_reasons = list(dict.fromkeys(reasons))[:3]
            card["search_match"] = {
                "score": round(score, 1),
                "reasons": dedup_reasons,
                "snippet": snippet,
            }
            results.append((score * 1_000_000 + float(card["updated_ts"]), card))

    results.sort(key=lambda x: x[0], reverse=True)
    ordered_cards = [r[1] for r in results]
    if not q_clean:
        if sort_by == "title":
            ordered_cards.sort(key=lambda c: (bool(c.get("is_sample")), (c.get("title") or "").lower()))
        elif sort_by == "progress":
            ordered_cards.sort(key=lambda c: (c.get("read_progress_pct", 0), c.get("read_units_count", 0), c.get("updated_ts", 0)), reverse=True)
        elif sort_by == "comments":
            ordered_cards.sort(key=lambda c: (c.get("total_annotations_count", 0), c.get("open_suggestions_count", 0), c.get("updated_ts", 0)), reverse=True)
        elif sort_by == "words":
            ordered_cards.sort(key=lambda c: (c.get("total_words", 0), c.get("updated_ts", 0)), reverse=True)
    return ordered_cards


def build_llm_comments_export(
    proj: Dict[str, Any],
    ver: Dict[str, Any],
    doc_record: Dict[str, Any],
    comments: List[Dict[str, Any]],
    export_format: str = "llm_prompt_pack",
    include_resolved: bool = False,
) -> Dict[str, Any]:
    """
    Exports comments & suggestions in 3 formats for LLM-assisted editing & version bumping:
    1. `llm_prompt_pack` (.md): Complete ready-to-paste LLM editorial revision prompt + conflict notes + source text
    2. `structured_sheet` (.json & .csv): Structured machine-readable ledger
    3. `inline_annotated` (.md): Full document text with non-destructive inline comment/suggestion tags (overlap-safe!)
    """
    ver_id = ver["version_id"]
    ver_label = ver.get("version_label", "v1.0")
    next_minor_label = f"v{ver.get('major', 1)}.{int(ver.get('minor', 0)) + 1}"
    next_major_label = f"v{int(ver.get('major', 1)) + 1}.0"

    filtered = [
        dict(c) for c in comments
        if c.get("version_id") == ver_id and (include_resolved or c.get("status", "open") == "open")
    ]
    enriched, conflict_groups = compute_comments_overlap_metadata(filtered)

    # Sort logically: Global -> Chapter -> Page -> Paragraph -> Character offset
    scope_rank = {"global": 0, "chapter": 1, "page": 2, "paragraph": 3, "sentence": 4, "phrase": 5, "word": 6}
    enriched.sort(key=lambda c: (
        0 if c.get("scope") == "global" else 1,
        int(c.get("page_number") or 0),
        int(c.get("unit_index") if c.get("unit_index") is not None else -1),
        int(c.get("char_start") if c.get("char_start") is not None else -1),
        scope_rank.get(c.get("scope", "paragraph"), 3),
    ))

    safe_slug = re.sub(r"[^A-Za-z0-9._-]+", "_", proj.get("title", "Document"))[:42].strip("_")

    if export_format == "structured_sheet":
        csv_buf = io.StringIO()
        writer = csv.writer(csv_buf)
        writer.writerow([
            "index", "comment_id", "version", "kind", "scope", "status",
            "page", "chapter", "unit_index", "char_start", "char_end",
            "selected_text", "suggested_replacement", "comment_note", "overlap_conflict",
        ])
        for idx, c in enumerate(enriched, start=1):
            writer.writerow([
                idx, c.get("comment_id"), ver_label, c.get("kind"), c.get("scope"), c.get("status"),
                c.get("page_number") or "", c.get("chapter_number") or "",
                c.get("unit_index") if c.get("unit_index") is not None else "",
                c.get("char_start") if c.get("char_start") is not None else "",
                c.get("char_end") if c.get("char_end") is not None else "",
                c.get("selected_text", ""), c.get("suggested_text", ""), c.get("content", ""),
                "CONFLICT" if c.get("suggestion_conflict") else "",
            ])
        payload_obj = {
            "project_id": proj["project_id"],
            "project_title": proj["title"],
            "current_version": ver_label,
            "recommended_next_versions": {"minor": next_minor_label, "major": next_major_label},
            "total_exported_items": len(enriched),
            "overlapping_conflict_groups": conflict_groups,
            "items": enriched,
            "csv_table": csv_buf.getvalue(),
        }
        content_str = json.dumps(payload_obj, indent=2, ensure_ascii=False)
        return {
            "format": "structured_sheet",
            "filename": f"{safe_slug}_{ver_label}_Comments_Structured.json",
            "mime_type": "application/json",
            "content": content_str,
            "csv_content": csv_buf.getvalue(),
            "count": len(enriched),
            "conflicts": conflict_groups,
        }

    if export_format == "inline_annotated":
        # Build inline-annotated document safely handling overlapping character spans!
        lines_out: List[str] = [
            f"<!-- KOKORO STUDIO v3.0 INLINE ANNOTATED MANUSCRIPT -->",
            f"<!-- Project: {proj['title']} | Source Version: {ver_label} | Target Version: {next_minor_label} or {next_major_label} -->",
            "",
        ]
        global_cmts = [c for c in enriched if c.get("scope") == "global"]
        if global_cmts:
            lines_out.append("<!-- GLOBAL DOCUMENT DIRECTIVES:")
            for gc in global_cmts:
                tag = "SUGGESTION" if gc.get("kind") == "suggestion" else "COMMENT"
                lines_out.append(f"  - [{tag}] {gc.get('content', '')} {('=> ' + gc['suggested_text']) if gc.get('suggested_text') else ''}")
            lines_out.append("-->\n")

        for p_obj in doc_record.get("pages") or []:
            p_num = p_obj["page_number"]
            page_cmts = [c for c in enriched if c.get("scope") in ("page", "chapter") and c.get("page_number") == p_num]
            for pc in page_cmts:
                tag = "SUGGESTION" if pc.get("kind") == "suggestion" else "COMMENT"
                lines_out.append(f"<!-- [{tag} · {pc.get('scope','page').upper()} {p_num}]: {pc.get('content','')} -->")

            for u_idx, para_txt in enumerate(p_obj.get("paragraphs") or []):
                unit_cmts = [
                    c for c in enriched
                    if c.get("page_number") == p_num and c.get("unit_index") == u_idx and c.get("scope") != "global"
                ]
                span_cmts = [
                    c for c in unit_cmts
                    if c.get("char_start") is not None and c.get("char_end") is not None and int(c["char_end"]) > int(c["char_start"])
                ]
                para_level_cmts = [c for c in unit_cmts if c not in span_cmts and c.get("scope") == "paragraph"]

                annotated_para = para_txt
                if span_cmts:
                    # Insert non-destructive markers from right to left by char_end so earlier offsets never shift!
                    by_end_desc = sorted(span_cmts, key=lambda x: (int(x["char_end"]), int(x["char_start"])), reverse=True)
                    for sc in by_end_desc:
                        ce = max(0, min(len(annotated_para), int(sc["char_end"])))
                        quote = sc.get("selected_text") or para_txt[max(0, int(sc["char_start"])):min(len(para_txt), int(sc["char_end"]))]
                        conflict_flag = " ⚠️OVERLAP-CONFLICT" if sc.get("suggestion_conflict") else ""
                        if sc.get("kind") == "suggestion":
                            repl = sc.get("suggested_text", "")
                            note = sc.get("content", "")
                            marker = f' <<SUGGESTION{conflict_flag} on "{quote}" => "{repl}"' + (f' | Note: {note}>>' if note else '>>')
                        else:
                            marker = f' <<COMMENT on "{quote}": {sc.get("content", "")}>>'
                        annotated_para = annotated_para[:ce] + marker + annotated_para[ce:]

                for plc in para_level_cmts:
                    tag = "SUGGESTION" if plc.get("kind") == "suggestion" else "COMMENT"
                    repl_part = f' => "{plc["suggested_text"]}"' if plc.get("suggested_text") else ""
                    annotated_para += f"\n<!-- [PARAGRAPH {tag}]{repl_part}: {plc.get('content', '')} -->"

                lines_out.append(annotated_para)
                lines_out.append("")

        md_str = "\n".join(lines_out).strip() + "\n"
        return {
            "format": "inline_annotated",
            "filename": f"{safe_slug}_{ver_label}_Inline_Annotated.md",
            "mime_type": "text/markdown",
            "content": md_str,
            "count": len(enriched),
            "conflicts": conflict_groups,
        }

    # Default Format 1: `llm_prompt_pack` (.md) — Comprehensive, High-Precision LLM Revision Prompt
    prompt_lines: List[str] = [
        f"# Editorial Revision Brief — {proj['title']} ({ver_label} → {next_minor_label} / {next_major_label})",
        "",
        "You are an expert literary & technical editor. Please revise the source document below by applying all **Suggestions** and addressing all **Comments** collected during our Kokoro Studio v3.0 listening review.",
        "",
        "## Revision Rules",
        "1. Preserve the author’s voice, chapter headings (`# ...`), and untouched paragraphs verbatim unless a Global/Chapter/Page comment asks for broader restructuring.",
        "2. Apply every **SUGGESTION** accurately at its anchored span or scope.",
        "3. Thoughtfully address every **COMMENT** while maintaining natural spoken cadence and clarity.",
        f"4. Output ONLY the complete, clean revised document text (ready to be imported directly into Kokoro Studio as **{next_minor_label}** or **{next_major_label}**), without commentary wrappers.",
        "",
    ]

    if conflict_groups:
        prompt_lines.extend([
            "## ⚠️ Overlapping Suggestion Conflicts Detected",
            f"There are **{len(conflict_groups)}** span(s) where multiple open suggestions overlap on the same phrase. Harmonize both editorial intentions smoothly into a single coherent phrasing:",
            "",
        ])

    prompt_lines.append(f"## Editorial Comments & Suggestions ({len(enriched)} Active Items)")
    prompt_lines.append("")

    if not enriched:
        prompt_lines.append("_No active comments or suggestions on this version._")
        prompt_lines.append("")
    else:
        for idx, c in enumerate(enriched, start=1):
            kind_badge = "💡 SUGGESTION" if c.get("kind") == "suggestion" else "💬 COMMENT"
            scope_str = (c.get("scope") or "paragraph").upper()
            loc_parts = []
            if c.get("chapter_number"):
                loc_parts.append(f"Chapter {c['chapter_number']}")
            if c.get("page_number"):
                loc_parts.append(f"Page {c['page_number']}")
            if c.get("unit_index") is not None:
                loc_parts.append(f"Paragraph #{int(c['unit_index']) + 1}")
            loc_str = " · ".join(loc_parts) if loc_parts else "Global Document"
            conflict_badge = " · ⚠️ OVERLAPPING CONFLICT" if c.get("suggestion_conflict") else (
                f" · Layered Overlap ({c.get('overlap_count')} items)" if int(c.get("overlap_count", 1)) > 1 else ""
            )

            prompt_lines.append(f"### {idx}. [{kind_badge}] ({scope_str} — {loc_str}{conflict_badge})")
            if c.get("selected_text"):
                ctx_b = c.get("context_before", "")
                ctx_a = c.get("context_after", "")
                prompt_lines.append(f"- **Target Span**: `{c['selected_text']}`")
                if ctx_b or ctx_a:
                    prompt_lines.append(f"- **Surrounding Context**: “…{ctx_b}**[{c['selected_text']}]**{ctx_a}…”")
            if c.get("kind") == "suggestion" and c.get("suggested_text"):
                prompt_lines.append(f"- **Proposed Replacement**: `{c['suggested_text']}`")
            if c.get("content"):
                prompt_lines.append(f"- **Editorial Note**: {c['content']}")
            prompt_lines.append("")

    prompt_lines.extend([
        "---",
        "",
        f"## Source Document ({ver_label} — `{ver.get('filename', '')}`)",
        "",
        "```markdown",
    ])
    for p_obj in doc_record.get("pages") or []:
        prompt_lines.append(f"<!-- Page {p_obj['page_number']} -->")
        for u_i, para in enumerate(p_obj.get("paragraphs") or []):
            prompt_lines.append(para)
            prompt_lines.append("")
    prompt_lines.append("```")
    prompt_lines.append("")

    full_pack = "\n".join(prompt_lines)
    return {
        "format": "llm_prompt_pack",
        "filename": f"{safe_slug}_{ver_label}_LLM_Revision_Prompt_Pack.md",
        "mime_type": "text/markdown",
        "content": full_pack,
        "count": len(enriched),
        "conflicts": conflict_groups,
        "recommended_next_versions": {"minor": next_minor_label, "major": next_major_label},
    }


# ==============================================================================
# FASTAPI APPLICATION & REQUEST SCHEMAS (v3.0)
# ==============================================================================
app = FastAPI(
    title="Kokoro-82M Document & PDF Reader Studio v3.0",
    version="3.0.0",
    description="Local Document & PDF Reader Studio v3.0 with Aoede Default Voice, Paragraph-First Streaming, Comments/Suggestions, Project Library & 25 Persona/Custom Themes"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class LoadPathRequest(BaseModel):
    path: Optional[str] = None
    filepath: Optional[str] = None
    task_id: Optional[str] = None


class SynthesizeRequest(BaseModel):
    text: str = ""
    voice: str = DEFAULT_VOICE
    speed: float = 1.0
    blend_voice: Optional[str] = None
    blend_weight: float = 0.0
    chunk_mode: str = "paragraph"
    strip_markdown: bool = True
    paragraphs: Optional[List[str]] = None
    lines: Optional[List[str]] = None
    title_hint: Optional[str] = None
    task_id: Optional[str] = None
    # v3.0 Generated Manifest Tracking Fields
    doc_id: Optional[str] = None
    scope: Optional[str] = None  # "unit" | "page" | "chapter"
    page_number: Optional[int] = None
    chapter_number: Optional[int] = None
    unit_index: Optional[int] = None


class ExportDocumentRequest(BaseModel):
    doc_id: str
    start_page: int = 1
    end_page: Optional[int] = None
    voice: str = DEFAULT_VOICE
    speed: float = 1.0
    blend_voice: Optional[str] = None
    blend_weight: float = 0.0
    chunk_mode: str = "paragraph"
    strip_markdown: bool = True
    announce_page_numbers: bool = False
    include_page_announcements: Optional[bool] = None
    task_id: Optional[str] = None


class UpdatePageTextRequest(BaseModel):
    text: str


class DiffCompareRequest(BaseModel):
    doc_a_id: Optional[str] = None
    doc_a_path: Optional[str] = None
    doc_a_text: Optional[str] = None
    text_a: Optional[str] = None
    doc_a_title: Optional[str] = None
    label_a: Optional[str] = "Document A (Original)"
    doc_b_id: Optional[str] = None
    doc_b_path: Optional[str] = None
    doc_b_text: Optional[str] = None
    text_b: Optional[str] = None
    doc_b_title: Optional[str] = None
    label_b: Optional[str] = "Document B (Revised)"
    chunk_mode: Optional[str] = None
    granularity: str = "paragraph"
    strip_markdown: bool = True
    task_id: Optional[str] = None


class PrepareAudioRequest(BaseModel):
    quality: str = "full_mp3"
    filename: Optional[str] = None
    task_id: Optional[str] = None


class StitchPageItem(BaseModel):
    page_number: int
    chapter_number: Optional[int] = None
    chapter_title: Optional[str] = None
    job_id: str
    segments: List[Dict[str, Any]] = []


class StitchPagesRequest(BaseModel):
    doc_id: Optional[str] = None
    filename: Optional[str] = "Full_Document"
    voice: str = DEFAULT_VOICE
    speed: float = 1.0
    chunk_mode: str = "paragraph"
    strip_markdown: bool = True
    pages: List[StitchPageItem] = []
    page_job_ids: Optional[List[str]] = None
    task_id: Optional[str] = None


class StitchUnitItem(BaseModel):
    page_number: int = 1
    chapter_number: Optional[int] = None
    unit_index: int = 0
    text: str = ""
    voice: str = DEFAULT_VOICE
    job_id: Optional[str] = None


class StitchUnitsRequest(BaseModel):
    doc_id: Optional[str] = None
    filename: Optional[str] = "Page_Narration"
    speed: float = 1.0
    chunk_mode: str = "paragraph"
    strip_markdown: bool = True
    blend_voice: Optional[str] = None
    blend_weight: float = 0.0
    synthesize_missing: bool = True
    units: List[StitchUnitItem]
    task_id: Optional[str] = None


# v3.0 Project Library & Comments Schemas
class CreateProjectRequest(BaseModel):
    title: str = ""
    description: str = ""
    tags: List[str] = []
    theme_id: str = "system_auto"
    default_voice: str = DEFAULT_VOICE
    doc_id: Optional[str] = None
    filepath: Optional[str] = None
    text: Optional[str] = None
    filename: Optional[str] = None


class PatchProjectRequest(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    tags: Optional[List[str]] = None
    theme_id: Optional[str] = None
    default_voice: Optional[str] = None
    voice_assignments: Optional[Dict[str, Any]] = None
    read_progress: Optional[Dict[str, Any]] = None
    resume_cursor: Optional[Dict[str, Any]] = None


class UpdateAutosaveSettingsRequest(BaseModel):
    max_backups: int = Field(default=DEFAULT_MAX_AUTOSAVE_BACKUPS, ge=1, le=1000)


class AutosaveProgressRequest(BaseModel):
    payload: Dict[str, Any]
    trigger: str = "auto"
    create_backup: bool = True
    force_backup: bool = False


class OpenAutosaveFolderRequest(BaseModel):
    project_id: Optional[str] = None
    doc_id: Optional[str] = None
    checkpoint_id: Optional[str] = None
    reveal_latest: bool = False


class CreateProjectVersionRequest(BaseModel):
    bump_type: str = "minor"  # "minor" (v1.0 -> v1.1) | "major" (v1.0 -> v2.0) | "custom"
    custom_version_label: Optional[str] = None
    notes: str = ""
    text: Optional[str] = None
    filepath: Optional[str] = None
    doc_id: Optional[str] = None
    filename: Optional[str] = None
    source_kind: str = "llm_revision"
    mark_open_suggestions_applied: bool = True
    carry_forward_open_comments: bool = True


class ProjectVersionDiffRequest(BaseModel):
    version_a_id: str
    version_b_id: str
    granularity: str = "paragraph"
    strip_markdown: bool = True
    task_id: Optional[str] = None


class CreateCommentRequest(BaseModel):
    project_id: str
    version_id: str
    version_label: Optional[str] = "v1.0"
    doc_id: Optional[str] = None
    kind: str = "comment"  # "comment" | "suggestion"
    scope: str = "paragraph"  # "word" | "phrase" | "sentence" | "paragraph" | "chapter" | "page" | "global"
    page_number: Optional[int] = None
    chapter_number: Optional[int] = None
    unit_index: Optional[int] = None
    unit_mode: str = "paragraph"
    char_start: Optional[int] = None
    char_end: Optional[int] = None
    selected_text: str = ""
    context_before: str = ""
    context_after: str = ""
    content: str = ""
    suggested_text: str = ""
    audio_timestamp_sec: Optional[float] = None
    voice_at_creation: Optional[str] = DEFAULT_VOICE


class PatchCommentRequest(BaseModel):
    project_id: str
    kind: Optional[str] = None
    scope: Optional[str] = None
    content: Optional[str] = None
    suggested_text: Optional[str] = None
    status: Optional[str] = None  # "open" | "resolved" | "applied" | "archived"
    char_start: Optional[int] = None
    char_end: Optional[int] = None
    selected_text: Optional[str] = None
    history_note: Optional[str] = None


class ResolveConflictRequest(BaseModel):
    project_id: str
    winning_comment_id: str
    competing_comment_ids: List[str]


class ExportCommentsRequest(BaseModel):
    project_id: str
    version_id: Optional[str] = None
    format: str = "llm_prompt_pack"  # "llm_prompt_pack" | "structured_sheet" | "inline_annotated"
    include_resolved: bool = False


class SaveCustomThemesRequest(BaseModel):
    themes: List[Dict[str, Any]]


# ==============================================================================
# INTELLIGENT DIFFERENTIAL READER ENGINE (DIFF READER v2.0 / v3.0)
# ==============================================================================
def _normalize_for_diff_key(text: str) -> str:
    s = re.sub(r"\s+", " ", (text or "").strip().lower())
    s = re.sub(r"[^\w\s]", "", s)
    return s.strip()


def _compute_text_similarity(a: str, b: str) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    na = _normalize_for_diff_key(a)
    nb = _normalize_for_diff_key(b)
    if na == nb:
        return 1.0
    seq_ratio = difflib.SequenceMatcher(None, na, nb).ratio()
    words_a = set(na.split())
    words_b = set(nb.split())
    if words_a or words_b:
        jaccard = len(words_a & words_b) / max(1, len(words_a | words_b))
        return 0.65 * seq_ratio + 0.35 * jaccard
    return seq_ratio


def compute_inline_word_diff(old_text: str, new_text: str) -> Tuple[List[Dict[str, str]], int, int]:
    words_a = (old_text or "").split()
    words_b = (new_text or "").split()
    matcher = difflib.SequenceMatcher(None, words_a, words_b, autojunk=False)
    tokens: List[Dict[str, str]] = []
    added_words = 0
    deleted_words = 0

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            chunk = " ".join(words_b[j1:j2])
            if chunk:
                tokens.append({"type": "equal", "text": chunk})
        elif tag == "delete":
            chunk = " ".join(words_a[i1:i2])
            if chunk:
                tokens.append({"type": "delete", "text": chunk})
                deleted_words += (i2 - i1)
        elif tag == "insert":
            chunk = " ".join(words_b[j1:j2])
            if chunk:
                tokens.append({"type": "insert", "text": chunk})
                added_words += (j2 - j1)
        elif tag == "replace":
            del_chunk = " ".join(words_a[i1:i2])
            ins_chunk = " ".join(words_b[j1:j2])
            if del_chunk:
                tokens.append({"type": "delete", "text": del_chunk})
                deleted_words += (i2 - i1)
            if ins_chunk:
                tokens.append({"type": "insert", "text": ins_chunk})
                added_words += (j2 - j1)

    return tokens, added_words, deleted_words


def _extract_units_for_diff(
    doc_obj: Optional[Dict[str, Any]],
    raw_text: Optional[str],
    fallback_label: str,
    granularity: str,
    strip_md: bool,
) -> Tuple[str, List[Dict[str, Any]]]:
    is_line = (granularity or "paragraph").lower() == "line"
    units: List[Dict[str, Any]] = []

    if doc_obj and "pages" in doc_obj:
        label = doc_obj.get("filename") or fallback_label
        chapters = doc_obj.get("chapters") or []
        for p in doc_obj["pages"]:
            p_num = p["page_number"]
            chap_title = None
            for c in chapters:
                if c["start_page"] <= p_num <= c["end_page"]:
                    chap_title = c["title"]
                    break
            raw_list = (p.get("lines") if is_line else p.get("paragraphs")) or []
            for u_idx, u_txt in enumerate(raw_list):
                cleaned = strip_markdown_formatting(u_txt, enabled=strip_md).strip()
                if not cleaned:
                    continue
                units.append({
                    "page_number": p_num,
                    "unit_index": u_idx,
                    "chapter_title": chap_title,
                    "raw_text": u_txt.strip(),
                    "clean_text": cleaned,
                    "norm_key": _normalize_for_diff_key(cleaned),
                })
        return label, units

    pages = paginate_plain_text(raw_text or "")
    for p in pages:
        p_num = p["page_number"]
        raw_list = (p.get("lines") if is_line else p.get("paragraphs")) or []
        for u_idx, u_txt in enumerate(raw_list):
            cleaned = strip_markdown_formatting(u_txt, enabled=strip_md).strip()
            if not cleaned:
                continue
            units.append({
                "page_number": p_num,
                "unit_index": u_idx,
                "chapter_title": None,
                "raw_text": u_txt.strip(),
                "clean_text": cleaned,
                "norm_key": _normalize_for_diff_key(cleaned),
            })
    return fallback_label, units


def compute_intelligent_document_diff(
    doc_a: Optional[Dict[str, Any]],
    doc_b: Optional[Dict[str, Any]],
    text_a: Optional[str] = None,
    text_b: Optional[str] = None,
    label_a: str = "Document A (Original)",
    label_b: str = "Document B (Revised)",
    granularity: str = "paragraph",
    strip_markdown: bool = True,
    task_id: Optional[str] = None,
) -> Dict[str, Any]:
    granularity = "line" if (granularity or "").lower() == "line" else "paragraph"
    update_task_progress(
        task_id, 12.0,
        f"Extracting {granularity}s from Document A & B",
        f"Segmenting {label_a} and {label_b} for differential analysis…",
        current_step=1,
        total_steps=4,
    )
    resolved_label_a, units_a = _extract_units_for_diff(doc_a, text_a, label_a, granularity, strip_markdown)
    resolved_label_b, units_b = _extract_units_for_diff(doc_b, text_b, label_b, granularity, strip_markdown)

    keys_a = [u["norm_key"] for u in units_a]
    keys_b = [u["norm_key"] for u in units_b]

    update_task_progress(
        task_id, 32.0,
        f"Aligning {len(units_a)} vs {len(units_b)} {granularity}s",
        f"Running SequenceMatcher structural alignment across {resolved_label_a} ↔ {resolved_label_b}…",
        current_step=2,
        total_steps=4,
    )
    matcher = difflib.SequenceMatcher(None, keys_a, keys_b, autojunk=False)
    diff_items: List[Dict[str, Any]] = []
    item_idx = 0
    change_order = 0

    def _emit_unchanged(ua: Dict[str, Any], ub: Dict[str, Any]):
        nonlocal item_idx
        txt = ub["clean_text"]
        diff_items.append({
            "index": item_idx,
            "change_number": None,
            "type": "unchanged",
            "badge_label": "Unchanged",
            "page_a": ua["page_number"],
            "unit_a": ua["unit_index"],
            "page_b": ub["page_number"],
            "unit_b": ub["unit_index"],
            "chapter_title": ub.get("chapter_title") or ua.get("chapter_title"),
            "old_text": ua["clean_text"],
            "new_text": txt,
            "display_text": txt,
            "similarity_pct": 100.0,
            "added_words": 0,
            "deleted_words": 0,
            "change_note": "Identical in both documents",
            "word_tokens": [{"type": "equal", "text": txt}],
            "spoken_variants": {"cue_new": txt, "before_after": txt, "pure": txt},
        })
        item_idx += 1

    def _emit_deleted(ua: Dict[str, Any]):
        nonlocal item_idx, change_order
        change_order += 1
        txt = ua["clean_text"]
        w_count = len(txt.split())
        diff_items.append({
            "index": item_idx,
            "change_number": change_order,
            "type": "deleted",
            "badge_label": "Deleted",
            "page_a": ua["page_number"],
            "unit_a": ua["unit_index"],
            "page_b": None,
            "unit_b": None,
            "chapter_title": ua.get("chapter_title"),
            "old_text": txt,
            "new_text": "",
            "display_text": txt,
            "similarity_pct": 0.0,
            "added_words": 0,
            "deleted_words": w_count,
            "change_note": f"Removed from {resolved_label_a} (Page {ua['page_number']}, -{w_count} words)",
            "word_tokens": [{"type": "delete", "text": txt}],
            "spoken_variants": {
                "cue_new": f"Deleted from page {ua['page_number']}: {txt}",
                "before_after": f"Deleted from page {ua['page_number']}: {txt}",
                "pure": txt,
            },
        })
        item_idx += 1

    def _emit_added(ub: Dict[str, Any]):
        nonlocal item_idx, change_order
        change_order += 1
        txt = ub["clean_text"]
        w_count = len(txt.split())
        diff_items.append({
            "index": item_idx,
            "change_number": change_order,
            "type": "added",
            "badge_label": "Added",
            "page_a": None,
            "unit_a": None,
            "page_b": ub["page_number"],
            "unit_b": ub["unit_index"],
            "chapter_title": ub.get("chapter_title"),
            "old_text": "",
            "new_text": txt,
            "display_text": txt,
            "similarity_pct": 0.0,
            "added_words": w_count,
            "deleted_words": 0,
            "change_note": f"Added in {resolved_label_b} (Page {ub['page_number']}, +{w_count} words)",
            "word_tokens": [{"type": "insert", "text": txt}],
            "spoken_variants": {
                "cue_new": f"Added on page {ub['page_number']}: {txt}",
                "before_after": f"Added on page {ub['page_number']}: {txt}",
                "pure": txt,
            },
        })
        item_idx += 1

    def _emit_modified(ua: Dict[str, Any], ub: Dict[str, Any], sim: float):
        nonlocal item_idx, change_order
        change_order += 1
        old_t = ua["clean_text"]
        new_t = ub["clean_text"]
        tokens, add_w, del_w = compute_inline_word_diff(old_t, new_t)
        sim_pct = round(sim * 100, 1)

        inserted_phrases = [tk["text"] for tk in tokens if tk["type"] == "insert"]
        deleted_phrases = [tk["text"] for tk in tokens if tk["type"] == "delete"]
        summary_parts = []
        if deleted_phrases and inserted_phrases:
            summary_parts.append(f"‘{' / '.join(deleted_phrases)[:42]}’ → ‘{' / '.join(inserted_phrases)[:42]}’")
        elif inserted_phrases:
            summary_parts.append(f"Added ‘{' '.join(inserted_phrases)[:45]}’")
        elif deleted_phrases:
            summary_parts.append(f"Removed ‘{' '.join(deleted_phrases)[:45]}’")

        detail_str = f" ({'; '.join(summary_parts)})" if summary_parts else ""
        diff_items.append({
            "index": item_idx,
            "change_number": change_order,
            "type": "modified",
            "badge_label": "Changed",
            "page_a": ua["page_number"],
            "unit_a": ua["unit_index"],
            "page_b": ub["page_number"],
            "unit_b": ub["unit_index"],
            "chapter_title": ub.get("chapter_title") or ua.get("chapter_title"),
            "old_text": old_t,
            "new_text": new_t,
            "display_text": new_t,
            "similarity_pct": sim_pct,
            "added_words": add_w,
            "deleted_words": del_w,
            "change_note": f"Modified on Page {ub['page_number']} ({sim_pct}% match · +{add_w}w, -{del_w}w){detail_str}",
            "word_tokens": tokens,
            "spoken_variants": {
                "cue_new": f"Changed on page {ub['page_number']}: {new_t}",
                "before_after": f"Changed on page {ub['page_number']}. Original was: {old_t}. Updated to: {new_t}",
                "pure": new_t,
            },
        })
        item_idx += 1

    opcodes = matcher.get_opcodes()
    total_ops = max(1, len(opcodes))
    for op_i, (tag, i1, i2, j1, j2) in enumerate(opcodes):
        pct = 40.0 + ((op_i + 1) / total_ops) * 48.0
        update_task_progress(
            task_id, pct,
            f"Computing inline word diffs ({op_i + 1}/{total_ops} blocks)",
            f"Analyzing {tag} block ({i2 - i1} original vs {j2 - j1} revised {granularity}s)…",
            current_step=op_i + 1,
            total_steps=total_ops,
        )
        if tag == "equal":
            for offset in range(i2 - i1):
                _emit_unchanged(units_a[i1 + offset], units_b[j1 + offset])
        elif tag == "delete":
            for ia in range(i1, i2):
                _emit_deleted(units_a[ia])
        elif tag == "insert":
            for jb in range(j1, j2):
                _emit_added(units_b[jb])
        elif tag == "replace":
            slice_a = units_a[i1:i2]
            slice_b = units_b[j1:j2]
            used_a = set()
            used_b = set()
            candidates: List[Tuple[float, int, int]] = []
            for idx_a, ua in enumerate(slice_a):
                for idx_b, ub in enumerate(slice_b):
                    sim = _compute_text_similarity(ua["clean_text"], ub["clean_text"])
                    if sim >= 0.36:
                        candidates.append((sim, idx_a, idx_b))
            candidates.sort(key=lambda x: x[0], reverse=True)
            pairings: Dict[int, Tuple[int, float]] = {}
            for sim, idx_a, idx_b in candidates:
                if idx_a not in used_a and idx_b not in used_b:
                    used_a.add(idx_a)
                    used_b.add(idx_b)
                    pairings[idx_b] = (idx_a, sim)

            cur_a = 0
            for idx_b, ub in enumerate(slice_b):
                if idx_b in pairings:
                    paired_a_idx, sim_val = pairings[idx_b]
                    while cur_a < paired_a_idx:
                        if cur_a not in used_a:
                            _emit_deleted(slice_a[cur_a])
                            used_a.add(cur_a)
                        cur_a += 1
                    if abs(sim_val - 1.0) < 1e-4 and slice_a[paired_a_idx]["clean_text"] == ub["clean_text"]:
                        _emit_unchanged(slice_a[paired_a_idx], ub)
                    else:
                        _emit_modified(slice_a[paired_a_idx], ub, sim_val)
                    cur_a = max(cur_a, paired_a_idx + 1)
                else:
                    _emit_added(ub)

            for rem_a in range(len(slice_a)):
                if rem_a not in used_a:
                    _emit_deleted(slice_a[rem_a])

    added_count = sum(1 for x in diff_items if x["type"] == "added")
    modified_count = sum(1 for x in diff_items if x["type"] == "modified")
    deleted_count = sum(1 for x in diff_items if x["type"] == "deleted")
    unchanged_count = sum(1 for x in diff_items if x["type"] == "unchanged")
    total_changes = added_count + modified_count + deleted_count

    update_task_progress(
        task_id, 93.0,
        "Building Executive Diff Digest",
        f"Found {total_changes} changes ({modified_count} modified, {added_count} added, {deleted_count} deleted)…",
        current_step=4,
        total_steps=4,
    )

    full_str_a = "\n".join(u["clean_text"] for u in units_a)
    full_str_b = "\n".join(u["clean_text"] for u in units_b)
    overall_sim = round(difflib.SequenceMatcher(None, full_str_a, full_str_b).ratio() * 100, 1) if (full_str_a or full_str_b) else 100.0

    affected_pages_b = sorted({x["page_b"] for x in diff_items if x["type"] in ("added", "modified") and x["page_b"] is not None})
    affected_pages_a = sorted({x["page_a"] for x in diff_items if x["type"] in ("deleted", "modified") and x["page_a"] is not None})

    unit_word_plural = "lines" if granularity == "line" else "paragraphs"
    if total_changes == 0:
        spoken_digest = (
            f"Differential comparison between {resolved_label_a} and {resolved_label_b} is complete. "
            f"No differences were found. All {unchanged_count} {unit_word_plural} are one hundred percent identical."
        )
    else:
        highlights = []
        for ch_item in [x for x in diff_items if x["type"] != "unchanged"][:3]:
            if ch_item["type"] == "modified":
                highlights.append(f"On page {ch_item['page_b']}, a passage was updated to: {ch_item['new_text'][:110]}")
            elif ch_item["type"] == "added":
                highlights.append(f"On page {ch_item['page_b']}, a new passage was added: {ch_item['new_text'][:110]}")
            elif ch_item["type"] == "deleted":
                highlights.append(f"From page {ch_item['page_a']}, a passage was removed: {ch_item['old_text'][:110]}")
        spoken_digest = (
            f"Differential comparison between {resolved_label_a} and {resolved_label_b} found {total_changes} total changes "
            f"with {overall_sim} percent overall similarity. "
            f"There are {modified_count} modified {unit_word_plural}, {added_count} newly added {unit_word_plural}, "
            f"and {deleted_count} deleted {unit_word_plural}, while {unchanged_count} {unit_word_plural} remained unchanged. "
            f"{' '.join(highlights)}"
        )

    for it in diff_items:
        it["block_index"] = it["index"]
        it["change_id"] = f"chg_{it['index']}"
        it["change_type"] = it["type"]
        it["change_summary"] = it.get("change_note", "")
        it["old_page"] = it["page_a"]
        it["new_page"] = it["page_b"]
        sv = it.get("spoken_variants") or {}
        it["spoken_cue_new"] = sv.get("cue_new") or it.get("new_text") or it.get("old_text") or ""
        it["spoken_before_after"] = sv.get("before_after") or it["spoken_cue_new"]
        it["spoken_pure"] = sv.get("pure") or it.get("new_text") or it.get("old_text") or ""
        w_diff = []
        for tk in it.get("word_tokens", []):
            w_diff.append({
                "type": tk["type"],
                "op": tk["type"],
                "text": tk["text"],
                "value": tk["text"],
            })
        it["word_tokens"] = w_diff
        it["word_diff"] = w_diff

    diff_id = hashlib.sha1(f"{resolved_label_a}:{resolved_label_b}:{granularity}:{strip_markdown}:{len(diff_items)}".encode("utf-8")).hexdigest()[:12]

    doc_a_meta = doc_a if doc_a else {
        "doc_id": f"text_a_{diff_id}",
        "filename": resolved_label_a,
        "filepath": f"text://{resolved_label_a}",
        "total_pages": 1,
        "total_words": len(full_str_a.split()),
    }
    doc_b_meta = doc_b if doc_b else {
        "doc_id": f"text_b_{diff_id}",
        "filename": resolved_label_b,
        "filepath": f"text://{resolved_label_b}",
        "total_pages": 1,
        "total_words": len(full_str_b.split()),
    }

    update_task_progress(
        task_id, 100.0,
        f"Diff Complete ({total_changes} changes · {overall_sim}% match)",
        f"{modified_count} modified · {added_count} added · {deleted_count} deleted · {unchanged_count} unchanged",
        current_step=4,
        total_steps=4,
        status="completed",
    )

    return {
        "diff_id": diff_id,
        "label_a": resolved_label_a,
        "label_b": resolved_label_b,
        "doc_a_id": doc_a_meta.get("doc_id"),
        "doc_b_id": doc_b_meta.get("doc_id"),
        "doc_a": {
            "doc_id": doc_a_meta.get("doc_id"),
            "label": resolved_label_a,
            "filename": doc_a_meta.get("filename", resolved_label_a),
            "filepath": doc_a_meta.get("filepath", ""),
            "total_pages": doc_a_meta.get("total_pages", 1),
            "total_words": doc_a_meta.get("total_words", len(full_str_a.split())),
        },
        "doc_b": {
            "doc_id": doc_b_meta.get("doc_id"),
            "label": resolved_label_b,
            "filename": doc_b_meta.get("filename", resolved_label_b),
            "filepath": doc_b_meta.get("filepath", ""),
            "total_pages": doc_b_meta.get("total_pages", 1),
            "total_words": doc_b_meta.get("total_words", len(full_str_b.split())),
        },
        "granularity": granularity,
        "strip_markdown": strip_markdown,
        "spoken_digest": spoken_digest,
        "executive_digest": spoken_digest,
        "stats": {
            "total_changes": total_changes,
            "added": added_count,
            "modified": modified_count,
            "deleted": deleted_count,
            "unchanged": unchanged_count,
            "total_items": len(diff_items),
            "similarity_percent": overall_sim,
        },
        "summary": {
            "total_changes": total_changes,
            "added_count": added_count,
            "modified_count": modified_count,
            "deleted_count": deleted_count,
            "unchanged_count": unchanged_count,
            "total_items": len(diff_items),
            "total_blocks": len(diff_items),
            "overall_similarity_pct": overall_sim,
            "affected_pages_a": affected_pages_a,
            "affected_pages_b": affected_pages_b,
            "spoken_digest": spoken_digest,
        },
        "items": diff_items,
        "blocks": diff_items,
    }


def ensure_sample_diff_pdfs() -> Tuple[Path, Path]:
    pdf_a_path = CACHE_DIR / "Kokoro_Sample_Draft_v1_Original.pdf"
    pdf_b_path = CACHE_DIR / "Kokoro_Sample_Draft_v2_Revised.pdf"
    if pdf_a_path.exists() and pdf_b_path.exists():
        return pdf_a_path, pdf_b_path

    try:
        import pymupdf

        doc_a = pymupdf.open()
        pages_a = [
            (
                "Chapter 1: Project Overview & Scope",
                "The Kokoro Audio Studio is a desktop reader designed for converting local documents into speech.\n\n"
                "The initial release supports standard text reading at a fixed speaking rate of 1.0x using the default English voice.\n\n"
                "Temporary draft note: Legacy raw line-by-line parsing is used for all PDF files and will be removed in v2."
            ),
            (
                "Chapter 2: Audio Export Specifications",
                "Users can export generated narration as an uncompressed WAV file stored in the local exports directory.\n\n"
                "All synthesis runs locally on your Mac with zero cloud API fees or external data transmission."
            ),
        ]
        for idx, (h, b) in enumerate(pages_a, start=1):
            p = doc_a.new_page(width=595, height=842)
            p.draw_rect(pymupdf.Rect(40, 40, 555, 92), color=(0.10, 0.14, 0.24), fill=(0.10, 0.14, 0.24))
            p.insert_text(pymupdf.Point(54, 72), h, fontsize=15, fontname="helv", color=(1, 1, 1))
            p.insert_textbox(pymupdf.Rect(52, 120, 543, 760), b, fontsize=13.5, fontname="helv", lineheight=1.55)
        doc_a.set_toc([[1, "Chapter 1: Project Overview & Scope", 1], [1, "Chapter 2: Audio Export Specifications", 2]])
        doc_a.save(str(pdf_a_path))
        doc_a.close()

        doc_b = pymupdf.open()
        pages_b = [
            (
                "Chapter 1: Project Overview & Scope",
                "The Kokoro Audio Studio is a desktop reader designed for converting local documents into speech.\n\n"
                "The v3.0 release defaults to Aoede, streams the first paragraph immediately while caching upcoming paragraphs, and supports 25 studio themes.\n\n"
                "New in v3.0: Multi-scope Comments & Suggestions with LLM version-up workflows and an organic Project Library."
            ),
            (
                "Chapter 2: Audio Export Specifications",
                "Users can export generated narration as Full-Resolution 320 kbps MP3, Compact 48 kbps Lo-Quality MP3, or Lossless 24kHz WAV.\n\n"
                "All synthesis runs locally on your Mac with zero cloud API fees or external data transmission.\n\n"
                "Markdown formatting symbols such as double asterisks and heading hashes are automatically omitted by default."
            ),
        ]
        for idx, (h, b) in enumerate(pages_b, start=1):
            p = doc_b.new_page(width=595, height=842)
            p.draw_rect(pymupdf.Rect(40, 40, 555, 92), color=(0.08, 0.22, 0.18), fill=(0.08, 0.22, 0.18))
            p.insert_text(pymupdf.Point(54, 72), h, fontsize=15, fontname="helv", color=(1, 1, 1))
            p.insert_textbox(pymupdf.Rect(52, 120, 543, 760), b, fontsize=13.5, fontname="helv", lineheight=1.55)
        doc_b.set_toc([[1, "Chapter 1: Project Overview & Scope", 1], [1, "Chapter 2: Audio Export Specifications", 2]])
        doc_b.save(str(pdf_b_path))
        doc_b.close()
    except Exception as e:
        print(f"Warning generating sample diff PDFs: {e}")

    return pdf_a_path, pdf_b_path


# ==============================================================================
# HTML FRONTEND ROUTES (v3.3 at `/`, v3.2 at `/v3.2`, v3.1 at `/v3.1`, v3.0 at `/v3`, v2.0 at `/v2`, v1.0 at `/v1`)
# ==============================================================================
@app.get("/favicon.svg")
def get_favicon():
    svg = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">
    <rect width="64" height="64" rx="14" fill="#1e1b2e"/>
    <path d="M16 17h15a6 6 0 0 1 6 6v24a4 4 0 0 0-4-4H16V17z" fill="#a78bfa"/>
    <path d="M45 21c3.6 3.1 5.6 6.5 5.6 10.5S48.6 38.9 45 42M40 26c2 1.6 3.2 3.5 3.2 5.5S42 35.4 40 37" stroke="#f5f3ff" stroke-width="3.2" stroke-linecap="round" fill="none"/>
    </svg>"""
    return Response(content=svg, media_type="image/svg+xml")


@app.get("/", response_class=HTMLResponse)
def index():
    html_path = SCRIPT_DIR / "KOKORO_CODE_Frontend_v3.3_20261116_FINAL.html"
    if not html_path.exists():
        return HTMLResponse("<h1>Frontend v3.3 HTML not found</h1>", status_code=500)
    return HTMLResponse(html_path.read_text(encoding="utf-8"))


@app.get("/v3.2", response_class=HTMLResponse)
def index_v32():
    """Preserves direct access to the untouched v3.2 frontend at /v3.2."""
    html_path = SCRIPT_DIR / "KOKORO_CODE_Frontend_v3.2_20261112_FINAL.html"
    if not html_path.exists():
        return RedirectResponse(url="/")
    return HTMLResponse(html_path.read_text(encoding="utf-8"))


@app.get("/v3.1", response_class=HTMLResponse)
def index_v31():
    """Preserves direct access to the untouched v3.1 frontend at /v3.1."""
    html_path = SCRIPT_DIR / "KOKORO_CODE_Frontend_v3.1_20261112_FINAL.html"
    if not html_path.exists():
        return RedirectResponse(url="/")
    return HTMLResponse(html_path.read_text(encoding="utf-8"))


@app.get("/v3", response_class=HTMLResponse)
@app.get("/v3.0", response_class=HTMLResponse)
def index_v3():
    """Preserves direct access to the untouched v3.0 frontend at /v3 and /v3.0."""
    html_path = SCRIPT_DIR / "KOKORO_CODE_Frontend_v3.0_20261112_FINAL.html"
    if not html_path.exists():
        return RedirectResponse(url="/")
    return HTMLResponse(html_path.read_text(encoding="utf-8"))


@app.get("/v2", response_class=HTMLResponse)
def index_v2():
    """Preserves direct access to the untouched v2.0 frontend at /v2."""
    html_path = SCRIPT_DIR / "KOKORO_CODE_Frontend_v2.0_20261023_FINAL.html"
    if not html_path.exists():
        return RedirectResponse(url="/")
    return HTMLResponse(html_path.read_text(encoding="utf-8"))


@app.get("/v1", response_class=HTMLResponse)
def index_v1():
    """Preserves direct access to the untouched v1.0 frontend at /v1."""
    html_path = SCRIPT_DIR / "KOKORO_CODE_Frontend_v1.0_20261021_FINAL.html"
    if not html_path.exists():
        return RedirectResponse(url="/")
    return HTMLResponse(html_path.read_text(encoding="utf-8"))


@app.get("/api/status")
def api_status():
    now_ts = time.time()
    effective_engine_status = "synthesizing" if ENGINE._active_syntheses > 0 else ENGINE._status
    legacy_defs = [
        ("v3.2", "/v3.2", "KOKORO_CODE_Frontend_v3.2_20261112_FINAL.html"),
        ("v3.1", "/v3.1", "KOKORO_CODE_Frontend_v3.1_20261112_FINAL.html"),
        ("v3.0", "/v3", "KOKORO_CODE_Frontend_v3.0_20261112_FINAL.html"),
        ("v2", "/v2", "KOKORO_CODE_Frontend_v2.0_20261023_FINAL.html"),
        ("v1", "/v1", "KOKORO_CODE_Frontend_v1.0_20261021_FINAL.html"),
    ]
    available_legacy = [{"label": lbl, "path": route} for lbl, route, fn in legacy_defs if (SCRIPT_DIR / fn).exists()]
    routes_map = {"v3.3": "/"}
    for item in available_legacy:
        routes_map[item["label"]] = item["path"]
    return {
        "ok": True,
        "server_live": True,
        "pid": os.getpid(),
        "uptime_sec": round(now_ts - SERVER_BOOT_TS, 1),
        "version": "3.3.0",
        "model": MODEL_REPO_ID,
        "engine_status": effective_engine_status,
        "model_loaded": bool(ENGINE._model is not None),
        "device": ENGINE._device,
        "loaded_pipelines": sorted(list(ENGINE._pipelines.keys())),
        "active_syntheses": ENGINE._active_syntheses,
        "total_syntheses": ENGINE._total_syntheses,
        "last_rtf": ENGINE._last_rtf,
        "last_duration_sec": ENGINE._last_duration_sec,
        "last_synthesis_ago_sec": round(now_ts - ENGINE._last_synthesis_ts, 1) if ENGINE._last_synthesis_ts else None,
        "engine_error": ENGINE._error,
        "sample_rate": SAMPLE_RATE,
        "voice_count": len(KOKORO_VOICES),
        "voices": KOKORO_VOICES,
        "default_voice": DEFAULT_VOICE,
        "default_chunk_mode": "paragraph",
        "default_strip_markdown": True,
        "custom_themes": load_custom_themes(),
        "autosave": get_autosave_settings_and_stats(),
        "available_legacy_routes": available_legacy,
        "routes": routes_map,
    }


@app.post("/api/engine/warmup")
def api_engine_warmup(voice: str = Query(default=DEFAULT_VOICE)):
    """Explicitly loads/verifies Kokoro-82M weights and requested voice pipeline in RAM."""
    t0 = time.time()
    vid = voice if voice in VOICE_MAP else DEFAULT_VOICE
    lang_code = VOICE_MAP.get(vid, {}).get("lang_code", "a")
    pipe = ENGINE.get_pipeline(lang_code)
    _ = pipe.load_voice(vid)
    return {
        "ok": True,
        "engine_status": ENGINE._status,
        "model_loaded": bool(ENGINE._model is not None),
        "voice_loaded": vid,
        "loaded_pipelines": sorted(list(ENGINE._pipelines.keys())),
        "warmup_ms": round((time.time() - t0) * 1000.0, 1),
    }


@app.get("/api/voices")
def api_voices():
    return {"voices": KOKORO_VOICES, "default_voice": DEFAULT_VOICE}


# ==============================================================================
# v3.0 CUSTOM THEMES PERSISTENCE ENDPOINTS
# ==============================================================================
@app.get("/api/themes/custom")
def api_get_custom_themes():
    return {"custom_themes": load_custom_themes()}


@app.put("/api/themes/custom")
def api_save_custom_themes(req: SaveCustomThemesRequest):
    saved = save_custom_themes(req.themes)
    return {"ok": True, "custom_themes": saved}


# ==============================================================================
# v3.0 PROJECT LIBRARY & MULTI-VERSION ENDPOINTS
# ==============================================================================
@app.get("/api/library/projects")
def api_list_or_search_projects(
    q: str = Query(default=""),
    filter: str = Query(default="all"),
    sort: str = Query(default="updated"),
):
    cards = search_projects_organically(query=q, filter_chip=filter, sort_by=sort)
    return {
        "ok": True,
        "query": q,
        "filter": filter,
        "sort": sort,
        "count": len(cards),
        "projects": cards,
    }


def _create_project_record_from_doc(
    doc_record: Dict[str, Any],
    title: str = "",
    description: str = "",
    tags: Optional[List[str]] = None,
    theme_id: str = "system_auto",
    default_voice: str = DEFAULT_VOICE,
    source_kind: str = "create",
) -> Dict[str, Any]:
    full_txt = _extract_full_text_from_doc(doc_record)
    pid = uuid.uuid4().hex[:12]
    vid = uuid.uuid4().hex[:12]
    now_str = time.strftime("%Y-%m-%d %H:%M:%S")
    now_ts = int(time.time())
    clean_title = (title or "").strip() or _clean_project_title(doc_record["filename"])
    resolved_tags = [t.strip() for t in (tags or []) if t and t.strip()]
    if not resolved_tags:
        ext_tag = (doc_record.get("extension") or ".md").lstrip(".") or "project"
        resolved_tags = [ext_tag]

    with LIBRARY_LOCK:
        _snapshot_file(vid).write_text(full_txt, encoding="utf-8")
        ver_obj = {
            "version_id": vid,
            "version_label": "v1.0",
            "major": 1,
            "minor": 0,
            "doc_id": doc_record["doc_id"],
            "filename": doc_record["filename"],
            "filepath": doc_record["filepath"],
            "extension": doc_record["extension"],
            "doc_type": doc_record["doc_type"],
            "sha256": doc_record["sha256"],
            "size_bytes": doc_record["size_bytes"],
            "total_pages": doc_record["total_pages"],
            "total_chapters": doc_record["total_chapters"],
            "total_paragraphs": doc_record["total_paragraphs"],
            "total_words": doc_record["total_words"],
            "notes": f"Initial v1.0 baseline ({doc_record['filename']})",
            "parent_version_id": None,
            "source_kind": source_kind,
            "created_at": now_str,
            "created_ts": now_ts,
        }
        proj = {
            "project_id": pid,
            "title": clean_title,
            "description": (description or "").strip() or f"{doc_record.get('doc_type', 'Document')} · {doc_record.get('total_pages', 1)} pages",
            "tags": resolved_tags,
            "is_sample": False,
            "theme_id": theme_id or "system_auto",
            "default_voice": default_voice if default_voice in VOICE_MAP else DEFAULT_VOICE,
            "voice_assignments": {"chapters": {}, "units": {}},
            "read_progress": {},
            "active_version_id": vid,
            "versions": [ver_obj],
            "created_at": now_str,
            "created_ts": now_ts,
            "updated_at": now_str,
            "updated_ts": now_ts,
        }
        _save_project_unlocked(proj)

    doc_record["project_id"] = pid
    doc_record["version_id"] = vid
    doc_record["version_label"] = "v1.0"
    doc_record["project_theme_id"] = proj["theme_id"]
    return {"ok": True, "project": proj, "active_version": ver_obj, "document": doc_record}


@app.post("/api/library/projects")
def api_create_project(req: CreateProjectRequest):
    doc_record: Optional[Dict[str, Any]] = None
    src_kind = "create"
    if req.filepath and req.filepath.strip():
        doc_record = load_document_from_path(req.filepath.strip(), auto_link_project=False)
        src_kind = "file"
    elif req.doc_id and req.doc_id in DOCUMENTS_STORE:
        candidate = DOCUMENTS_STORE[req.doc_id]
        # Guard against accidentally cloning SampleGuide.pdf into a custom-named project unless user explicitly named it Sample
        if _is_sample_guide_doc(candidate.get("filename", ""), candidate.get("filepath", "")) and "sample" not in (req.title or "").lower():
            safe_slug = re.sub(r"[^A-Za-z0-9_-]+", "_", req.title or "Manuscript")[:36] or "Manuscript"
            doc_record = build_doc_record_from_text(
                f"# {req.title or 'New Manuscript'}\n\nStart writing or click **➕ Add Version** to upload a manuscript file.",
                filename=f"{safe_slug}_v1.0.md",
            )
        else:
            doc_record = candidate
            src_kind = "reader_doc"
    elif req.text and req.text.strip():
        fname = req.filename or f"{re.sub(r'[^A-Za-z0-9_-]+', '_', req.title)[:36] or 'Manuscript'}_v1.0.md"
        doc_record = build_doc_record_from_text(req.text, filename=fname)
        src_kind = "paste"
    else:
        safe_slug = re.sub(r"[^A-Za-z0-9_-]+", "_", req.title or "Manuscript")[:36] or "Manuscript"
        doc_record = build_doc_record_from_text(
            f"# {req.title or 'New Manuscript'}\n\nStart writing or click **➕ Add Version** to upload a manuscript file.",
            filename=f"{safe_slug}_v1.0.md",
        )

    return _create_project_record_from_doc(
        doc_record=doc_record,
        title=req.title,
        description=req.description,
        tags=req.tags,
        theme_id=req.theme_id,
        default_voice=req.default_voice,
        source_kind=src_kind,
    )


@app.post("/api/library/projects/upload")
def api_create_project_with_upload(
    file: UploadFile = File(...),
    title: str = Form(default=""),
    description: str = Form(default=""),
    tags: str = Form(default=""),
    theme_id: str = Form(default="system_auto"),
    default_voice: str = Form(default=DEFAULT_VOICE),
):
    safe_name = re.sub(r"[^A-Za-z0-9._ -]", "_", file.filename or "manuscript.pdf")
    dest_path = CACHE_DIR / f"{int(time.time())}_{safe_name}"
    dest_path.write_bytes(file.file.read())
    doc_record = load_document_from_path(str(dest_path), auto_link_project=False, display_filename=safe_name)
    parsed_tags = [t.strip() for t in re.split(r"[,;]+", tags or "") if t.strip()]
    return _create_project_record_from_doc(
        doc_record=doc_record,
        title=title,
        description=description,
        tags=parsed_tags,
        theme_id=theme_id,
        default_voice=default_voice,
        source_kind="upload",
    )


@app.post("/api/library/projects/ensure-for-doc")
def api_ensure_project_for_doc(doc_id: str = Query(...)):
    if doc_id not in DOCUMENTS_STORE:
        raise HTTPException(status_code=404, detail="Document not found in memory.")
    doc = DOCUMENTS_STORE[doc_id]
    res = ensure_project_for_document(doc)
    doc["project_id"] = res["project"]["project_id"]
    doc["version_id"] = res["active_version"]["version_id"]
    doc["version_label"] = res["active_version"]["version_label"]
    doc["project_theme_id"] = res["project"].get("theme_id", "system_auto")
    return {"ok": True, **res}


@app.get("/api/library/projects/{project_id}")
def api_get_project_detail(project_id: str):
    with LIBRARY_LOCK:
        proj = _load_project_unlocked(project_id)
        if not proj:
            raise HTTPException(status_code=404, detail="Project not found.")
        comments = _load_project_comments_unlocked(project_id)
    enriched_cmts, conflicts = compute_comments_overlap_metadata([dict(c) for c in comments])
    card = build_project_summary_card(proj, enriched_cmts)
    return {
        "ok": True,
        "project": proj,
        "summary": card,
        "comments": enriched_cmts,
        "conflicts": conflicts,
    }


@app.patch("/api/library/projects/{project_id}")
def api_patch_project(project_id: str, req: PatchProjectRequest):
    with LIBRARY_LOCK:
        proj = _load_project_unlocked(project_id)
        if not proj:
            raise HTTPException(status_code=404, detail="Project not found.")
        if req.title is not None and req.title.strip():
            proj["title"] = req.title.strip()
        if req.description is not None:
            proj["description"] = req.description
        if req.tags is not None:
            proj["tags"] = req.tags
        if req.theme_id is not None:
            proj["theme_id"] = req.theme_id
        if req.default_voice is not None and req.default_voice in VOICE_MAP:
            proj["default_voice"] = req.default_voice
        if req.voice_assignments is not None:
            cur_va = proj.setdefault("voice_assignments", {"chapters": {}, "units": {}})
            if "chapters" in req.voice_assignments:
                cur_va["chapters"] = req.voice_assignments["chapters"]
            if "units" in req.voice_assignments:
                cur_va["units"] = req.voice_assignments["units"]
        if req.read_progress is not None:
            cur_rp = proj.setdefault("read_progress", {})
            cur_rp.update(req.read_progress)
        if req.resume_cursor is not None:
            proj["resume_cursor"] = req.resume_cursor
            rc_ver = req.resume_cursor.get("version_id") or proj.get("active_version_id")
            if rc_ver:
                proj.setdefault("resume_cursors", {})[rc_ver] = req.resume_cursor
        _save_project_unlocked(proj)
    return {"ok": True, "project": proj, "autosaved_at": proj["updated_at"]}


@app.delete("/api/library/projects/{project_id}")
def api_delete_project(project_id: str):
    with LIBRARY_LOCK:
        proj = _load_project_unlocked(project_id)
        if not proj:
            raise HTTPException(status_code=404, detail="Project not found.")
        for v in proj.get("versions") or []:
            sp = _snapshot_file(v["version_id"])
            if sp.exists():
                sp.unlink(missing_ok=True)
        _project_file(project_id).unlink(missing_ok=True)
        _comments_file(project_id).unlink(missing_ok=True)
        as_dir = _find_or_create_doc_autosave_dir_unlocked(
            _resolve_doc_autosave_key(project_id=project_id),
            title_hint=proj.get("title", ""),
            create=False,
        )
        if as_dir and as_dir.exists():
            shutil.rmtree(as_dir, ignore_errors=True)
        ids = [x for x in _load_projects_index_unlocked() if x != project_id]
        _save_projects_index_unlocked(ids)
    return {"ok": True, "deleted_project_id": project_id}


# ==============================================================================
# v3.3 PER-DOCUMENT `.JSON` AUTO-SAVE, CHECKPOINT PICKER & FINDER ENDPOINTS
# ==============================================================================
@app.get("/api/autosave/settings")
def api_get_autosave_settings():
    return get_autosave_settings_and_stats()


@app.put("/api/autosave/settings")
def api_put_autosave_settings(req: UpdateAutosaveSettingsRequest):
    return update_autosave_max_backups(req.max_backups)


@app.post("/api/autosave/save")
def api_post_autosave_progress(req: AutosaveProgressRequest):
    return record_document_progress_autosave(
        payload=req.payload,
        trigger=req.trigger,
        create_backup=req.create_backup,
        force_backup=req.force_backup,
    )


@app.post("/api/autosave/beacon")
async def api_post_autosave_beacon(request: Request):
    try:
        raw_bytes = await request.body()
        if not raw_bytes:
            return {"ok": False, "detail": "Empty beacon body."}
        parsed = json.loads(raw_bytes.decode("utf-8"))
        if isinstance(parsed, dict) and "payload" in parsed and isinstance(parsed["payload"], dict):
            return record_document_progress_autosave(
                payload=parsed["payload"],
                trigger=str(parsed.get("trigger") or "unload"),
                create_backup=bool(parsed.get("create_backup", True)),
                force_backup=bool(parsed.get("force_backup", False)),
            )
        elif isinstance(parsed, dict):
            return record_document_progress_autosave(
                payload=parsed,
                trigger="unload",
                create_backup=True,
                force_backup=False,
            )
    except Exception as exc:
        return {"ok": False, "detail": str(exc)}
    return {"ok": False}


@app.get("/api/autosave/checkpoints")
def api_get_autosave_checkpoints(
    project_id: Optional[str] = Query(default=None),
    doc_id: Optional[str] = Query(default=None),
    version_id: Optional[str] = Query(default=None),
):
    return list_document_checkpoints(project_id=project_id, doc_id=doc_id, version_id=version_id)


@app.get("/api/library/projects/{project_id}/checkpoints")
def api_get_project_checkpoints(
    project_id: str,
    version_id: Optional[str] = Query(default=None),
):
    return list_document_checkpoints(project_id=project_id, version_id=version_id)


@app.get("/api/autosave/checkpoint-payload")
def api_get_autosave_checkpoint_payload(
    checkpoint_id: str = Query(default="latest"),
    project_id: Optional[str] = Query(default=None),
    doc_id: Optional[str] = Query(default=None),
):
    with LIBRARY_LOCK:
        proj_obj = _load_project_unlocked(project_id) if project_id else None
        title_hint = (proj_obj.get("title") if proj_obj else "") or "document"
        target_key = _resolve_doc_autosave_key(project_id=project_id, doc_id=doc_id)
        doc_dir = _find_or_create_doc_autosave_dir_unlocked(target_key, title_hint=title_hint, create=False)
        if not doc_dir or not doc_dir.exists():
            raise HTTPException(status_code=404, detail="No auto-save folder exists for this document yet.")

        if checkpoint_id == "latest":
            target_file = doc_dir / "latest-progress.json"
        else:
            safe_cid = re.sub(r"[^A-Za-z0-9._-]", "", checkpoint_id)
            if safe_cid.endswith(".json"):
                safe_cid = safe_cid[:-5]
            target_file = doc_dir / f"{safe_cid}.json"

        if not target_file.exists():
            raise HTTPException(status_code=404, detail=f"Checkpoint file not found: {target_file.name}")

        payload = json.loads(target_file.read_text(encoding="utf-8"))
        summary = _extract_checkpoint_summary_from_payload(
            payload,
            checkpoint_id="latest" if checkpoint_id == "latest" else target_file.stem,
            filename=target_file.name,
            filepath=str(target_file),
            size_bytes=target_file.stat().st_size,
        )
    return {
        "ok": True,
        "checkpoint": summary,
        "payload": payload,
    }


@app.delete("/api/autosave/checkpoints/{checkpoint_id}")
def api_delete_autosave_checkpoint(
    checkpoint_id: str,
    project_id: Optional[str] = Query(default=None),
    doc_id: Optional[str] = Query(default=None),
):
    with LIBRARY_LOCK:
        proj_obj = _load_project_unlocked(project_id) if project_id else None
        title_hint = (proj_obj.get("title") if proj_obj else "") or "document"
        target_key = _resolve_doc_autosave_key(project_id=project_id, doc_id=doc_id)
        doc_dir = _find_or_create_doc_autosave_dir_unlocked(target_key, title_hint=title_hint, create=False)
        if not doc_dir or not doc_dir.exists():
            raise HTTPException(status_code=404, detail="Auto-save folder not found.")
        safe_cid = re.sub(r"[^A-Za-z0-9._-]", "", checkpoint_id)
        if safe_cid.endswith(".json"):
            safe_cid = safe_cid[:-5]
        target_file = doc_dir / f"{safe_cid}.json"
        if target_file.exists():
            target_file.unlink(missing_ok=True)
        entries = [x for x in _load_or_rebuild_checkpoints_index_unlocked(doc_dir) if x.get("checkpoint_id") != safe_cid]
        _atomic_write_json(doc_dir / "checkpoints-index.json", entries)
    return {"ok": True, "deleted_checkpoint_id": safe_cid, "remaining_count": len(entries)}


@app.get("/api/autosave/read-file")
def api_read_autosave_json_file(filepath: str = Query(...)):
    PROGRESS_SAVES_DIR.mkdir(parents=True, exist_ok=True)
    resolved = Path(os.path.expanduser(filepath.strip())).resolve()
    saves_root = PROGRESS_SAVES_DIR.resolve()
    if not str(resolved).startswith(str(saves_root) + os.sep) or resolved.suffix.lower() != ".json":
        raise HTTPException(status_code=403, detail="Only .json files inside the Library progress-saves folder can be loaded here.")
    if not resolved.exists() or not resolved.is_file():
        raise HTTPException(status_code=404, detail=f"Checkpoint file not found: {resolved.name}")
    payload = json.loads(resolved.read_text(encoding="utf-8"))
    summary = _extract_checkpoint_summary_from_payload(
        payload,
        checkpoint_id="latest" if resolved.name == "latest-progress.json" else resolved.stem,
        filename=resolved.name,
        filepath=str(resolved),
        size_bytes=resolved.stat().st_size,
    )
    return {"ok": True, "checkpoint": summary, "payload": payload}


@app.post("/api/autosave/open-folder")
def api_open_autosave_folder_in_finder(req: Optional[OpenAutosaveFolderRequest] = Body(default=None)):
    PROGRESS_SAVES_DIR.mkdir(parents=True, exist_ok=True)
    pid = req.project_id if req else None
    did = req.doc_id if req else None
    cid = req.checkpoint_id if req else None
    reveal_latest = bool(req.reveal_latest) if req else False

    target_dir = PROGRESS_SAVES_DIR
    reveal_file: Optional[Path] = None

    if pid or did:
        with LIBRARY_LOCK:
            proj_obj = _load_project_unlocked(pid) if pid else None
            title_hint = (proj_obj.get("title") if proj_obj else "") or "document"
            target_key = _resolve_doc_autosave_key(project_id=pid, doc_id=did)
            doc_dir = _find_or_create_doc_autosave_dir_unlocked(target_key, title_hint=title_hint, create=True)
            if doc_dir:
                target_dir = doc_dir
                if cid:
                    safe_cid = re.sub(r"[^A-Za-z0-9._-]", "", cid)
                    if safe_cid.endswith(".json"):
                        safe_cid = safe_cid[:-5]
                    cand = doc_dir / ("latest-progress.json" if safe_cid == "latest" else f"{safe_cid}.json")
                    if cand.exists():
                        reveal_file = cand
                elif reveal_latest:
                    cand = doc_dir / "latest-progress.json"
                    if cand.exists():
                        reveal_file = cand

    try:
        if sys.platform == "darwin":
            if reveal_file and reveal_file.exists():
                subprocess.Popen(["open", "-R", str(reveal_file)])
                return {"ok": True, "mode": "reveal_file", "opened_path": str(reveal_file), "folder_path": str(target_dir)}
            subprocess.Popen(["open", str(target_dir)])
            return {"ok": True, "mode": "open_folder", "opened_path": str(target_dir), "folder_path": str(target_dir)}
        elif os.name == "nt":
            os.startfile(str(target_dir))  # type: ignore[attr-defined]
            return {"ok": True, "mode": "open_folder", "opened_path": str(target_dir), "folder_path": str(target_dir)}
        else:
            subprocess.Popen(["xdg-open", str(target_dir)])
            return {"ok": True, "mode": "open_folder", "opened_path": str(target_dir), "folder_path": str(target_dir)}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to open folder in file manager: {exc}")


def _append_new_version_to_project(
    project_id: str,
    new_doc: Dict[str, Any],
    bump_type: str = "minor",
    custom_version_label: Optional[str] = None,
    notes: str = "",
    source_kind: str = "llm_revision",
    mark_open_suggestions_applied: bool = True,
    carry_forward_open_comments: bool = True,
) -> Dict[str, Any]:
    full_txt = _extract_full_text_from_doc(new_doc)
    now_str = time.strftime("%Y-%m-%d %H:%M:%S")
    now_ts = int(time.time())

    with LIBRARY_LOCK:
        proj = _load_project_unlocked(project_id)
        if not proj:
            raise HTTPException(status_code=404, detail="Project not found.")

        versions = proj.get("versions") or []
        prev_ver = versions[-1] if versions else {"version_id": None, "major": 1, "minor": 0, "version_label": "v1.0"}
        prev_vid = proj.get("active_version_id") or prev_ver.get("version_id")
        parent_ver = next((v for v in versions if v["version_id"] == prev_vid), prev_ver)

        max_major = max((int(v.get("major", 1)) for v in versions), default=1)
        same_major_minors = [int(v.get("minor", 0)) for v in versions if int(v.get("major", 1)) == max_major]
        max_minor = max(same_major_minors, default=0)

        if bump_type == "major":
            new_major = max_major + 1
            new_minor = 0
            ver_label = f"v{new_major}.0"
        elif bump_type == "custom" and custom_version_label:
            ver_label = custom_version_label.strip()
            m = re.match(r"^v?(\d+)\.(\d+)", ver_label)
            new_major = int(m.group(1)) if m else max_major
            new_minor = int(m.group(2)) if m else (max_minor + 1)
            if not ver_label.startswith("v"):
                ver_label = f"v{ver_label}"
        else:
            new_major = max_major
            new_minor = max_minor + 1
            ver_label = f"v{new_major}.{new_minor}"

        new_vid = uuid.uuid4().hex[:12]
        _snapshot_file(new_vid).write_text(full_txt, encoding="utf-8")

        ver_obj = {
            "version_id": new_vid,
            "version_label": ver_label,
            "major": new_major,
            "minor": new_minor,
            "doc_id": new_doc["doc_id"],
            "filename": new_doc["filename"],
            "filepath": new_doc["filepath"],
            "extension": new_doc["extension"],
            "doc_type": new_doc["doc_type"],
            "sha256": new_doc["sha256"],
            "size_bytes": new_doc["size_bytes"],
            "total_pages": new_doc["total_pages"],
            "total_chapters": new_doc["total_chapters"],
            "total_paragraphs": new_doc["total_paragraphs"],
            "total_words": new_doc["total_words"],
            "notes": notes.strip() or f"Updated to {ver_label} ({source_kind.replace('_', ' ')})",
            "parent_version_id": parent_ver.get("version_id"),
            "source_kind": source_kind,
            "created_at": now_str,
            "created_ts": now_ts,
        }
        versions.append(ver_obj)
        proj["versions"] = versions
        proj["active_version_id"] = new_vid
        _save_project_unlocked(proj)

        # Update comment lifecycle & optionally carry forward still-open comments!
        comments = _load_project_comments_unlocked(project_id)
        carried_count = 0
        applied_count = 0
        new_carried_items: List[Dict[str, Any]] = []

        for c in comments:
            if c.get("version_id") != prev_vid:
                continue
            if c.get("status", "open") != "open":
                continue

            if mark_open_suggestions_applied and c.get("kind") == "suggestion":
                c["status"] = "applied"
                c["updated_at"] = now_str
                c.setdefault("history", []).append({
                    "action": "applied",
                    "timestamp": now_str,
                    "version_label": ver_label,
                    "note": f"Marked applied upon creating {ver_label}",
                })
                applied_count += 1
            elif carry_forward_open_comments:
                # Re-anchor to matching page/paragraph/span in new_doc if possible
                cloned = json.loads(json.dumps(c))
                cloned["comment_id"] = f"cmt-{uuid.uuid4().hex[:10]}"
                cloned["version_id"] = new_vid
                cloned["version_label"] = ver_label
                cloned["doc_id"] = new_doc["doc_id"]
                cloned["carried_from_version"] = parent_ver.get("version_label", "v1.0")
                cloned["carried_from_comment_id"] = c["comment_id"]
                cloned["updated_at"] = now_str

                sel = (c.get("selected_text") or "").strip()
                reanchored = False
                if sel and new_doc.get("pages"):
                    for p_obj in new_doc["pages"]:
                        for u_i, p_str in enumerate(p_obj.get("paragraphs") or []):
                            pos = p_str.find(sel)
                            if pos >= 0:
                                cloned["page_number"] = p_obj["page_number"]
                                cloned["unit_index"] = u_i
                                cloned["char_start"] = pos
                                cloned["char_end"] = pos + len(sel)
                                reanchored = True
                                break
                        if reanchored:
                            break
                if not reanchored and cloned.get("page_number"):
                    clamped_p = min(int(cloned["page_number"]), len(new_doc.get("pages") or [1]))
                    cloned["page_number"] = max(1, clamped_p)
                    if cloned.get("scope") in ("word", "phrase", "sentence"):
                        cloned["scope"] = "paragraph"
                        cloned["char_start"] = None
                        cloned["char_end"] = None

                cloned.setdefault("history", []).append({
                    "action": "carried_forward",
                    "timestamp": now_str,
                    "version_label": ver_label,
                    "note": f"Carried forward from {parent_ver.get('version_label', 'previous version')} to {ver_label}",
                })
                new_carried_items.append(cloned)
                carried_count += 1

        if applied_count > 0 or new_carried_items:
            comments.extend(new_carried_items)
            _save_project_comments_unlocked(project_id, comments)

    new_doc["project_id"] = project_id
    new_doc["version_id"] = new_vid
    new_doc["version_label"] = ver_label
    new_doc["project_theme_id"] = proj.get("theme_id", "system_auto")

    return {
        "ok": True,
        "project": proj,
        "new_version": ver_obj,
        "previous_version": parent_ver,
        "document": new_doc,
        "applied_suggestions_count": applied_count,
        "carried_comments_count": carried_count,
    }


@app.post("/api/library/projects/{project_id}/versions")
def api_create_project_version(project_id: str, req: CreateProjectVersionRequest):
    if req.doc_id and req.doc_id in DOCUMENTS_STORE:
        new_doc = DOCUMENTS_STORE[req.doc_id]
    elif req.filepath and req.filepath.strip():
        new_doc = load_document_from_path(req.filepath.strip(), auto_link_project=False)
    elif req.text and req.text.strip():
        with LIBRARY_LOCK:
            proj = _load_project_unlocked(project_id)
            if not proj:
                raise HTTPException(status_code=404, detail="Project not found.")
            base_slug = re.sub(r"[^A-Za-z0-9_-]+", "_", proj["title"])[:36].strip("_") or "Document"
        fname = req.filename or f"{base_slug}_Revised.md"
        new_doc = build_doc_record_from_text(req.text, filename=fname)
    else:
        raise HTTPException(status_code=400, detail="Provide revised `text`, `filepath`, or `doc_id` to create a new version.")

    return _append_new_version_to_project(
        project_id=project_id,
        new_doc=new_doc,
        bump_type=req.bump_type,
        custom_version_label=req.custom_version_label,
        notes=req.notes,
        source_kind=req.source_kind,
        mark_open_suggestions_applied=req.mark_open_suggestions_applied,
        carry_forward_open_comments=req.carry_forward_open_comments,
    )


@app.post("/api/library/projects/{project_id}/versions/upload")
def api_upload_project_version(
    project_id: str,
    file: UploadFile = File(...),
    bump_type: str = Form(default="minor"),
    custom_version_label: Optional[str] = Form(default=None),
    notes: str = Form(default=""),
    mark_open_suggestions_applied: bool = Form(default=True),
    carry_forward_open_comments: bool = Form(default=True),
):
    safe_name = re.sub(r"[^A-Za-z0-9._ -]", "_", file.filename or "revised_version.md")
    dest_path = CACHE_DIR / f"{int(time.time())}_{safe_name}"
    dest_path.write_bytes(file.file.read())
    new_doc = load_document_from_path(str(dest_path), auto_link_project=False, display_filename=safe_name)
    return _append_new_version_to_project(
        project_id=project_id,
        new_doc=new_doc,
        bump_type=bump_type,
        custom_version_label=custom_version_label,
        notes=notes or f"Uploaded revised file {safe_name}",
        source_kind="llm_upload",
        mark_open_suggestions_applied=mark_open_suggestions_applied,
        carry_forward_open_comments=carry_forward_open_comments,
    )


@app.post("/api/library/projects/{project_id}/versions/{version_id}/activate")
def api_activate_project_version(project_id: str, version_id: str):
    with LIBRARY_LOCK:
        proj = _load_project_unlocked(project_id)
        if not proj:
            raise HTTPException(status_code=404, detail="Project not found.")
        ver = next((v for v in (proj.get("versions") or []) if v["version_id"] == version_id), None)
        if not ver:
            raise HTTPException(status_code=404, detail="Version not found in project.")
        proj["active_version_id"] = version_id
        doc = activate_project_version_into_memory(proj, ver)
        _save_project_unlocked(proj)
        comments = _load_project_comments_unlocked(project_id)

    enriched_cmts, conflicts = compute_comments_overlap_metadata([dict(c) for c in comments])
    return {
        "ok": True,
        "project": proj,
        "active_version": ver,
        "document": doc,
        "comments": enriched_cmts,
        "conflicts": conflicts,
    }


@app.post("/api/library/projects/{project_id}/diff")
def api_diff_project_versions(project_id: str, req: ProjectVersionDiffRequest):
    with LIBRARY_LOCK:
        proj = _load_project_unlocked(project_id)
        if not proj:
            raise HTTPException(status_code=404, detail="Project not found.")
        ver_a = next((v for v in (proj.get("versions") or []) if v["version_id"] == req.version_a_id), None)
        ver_b = next((v for v in (proj.get("versions") or []) if v["version_id"] == req.version_b_id), None)
        if not ver_a or not ver_b:
            raise HTTPException(status_code=404, detail="Selected version(s) not found.")
        doc_a = activate_project_version_into_memory(proj, ver_a)
        doc_b = activate_project_version_into_memory(proj, ver_b)

    diff_res = compute_intelligent_document_diff(
        doc_a=doc_a,
        doc_b=doc_b,
        label_a=f"{proj['title']} ({ver_a['version_label']})",
        label_b=f"{proj['title']} ({ver_b['version_label']})",
        granularity=req.granularity,
        strip_markdown=req.strip_markdown,
        task_id=req.task_id,
    )
    return {**diff_res, "doc_a": doc_a, "doc_b": doc_b}


# ==============================================================================
# v3.0 COMMENTS, SUGGESTIONS, OVERLAP SOLVER, HISTORY & LLM EXPORT ENDPOINTS
# ==============================================================================
@app.get("/api/comments")
def api_get_comments(
    project_id: str = Query(...),
    version_id: Optional[str] = Query(default=None),
    include_all_versions: bool = Query(default=True),
):
    with LIBRARY_LOCK:
        all_cmts = _load_project_comments_unlocked(project_id)

    enriched_all, all_conflicts = compute_comments_overlap_metadata([dict(c) for c in all_cmts])
    active_cmts = [c for c in enriched_all if (not version_id or c.get("version_id") == version_id)]
    active_conflicts = [
        cg for cg in all_conflicts
        if any(cid in {c["comment_id"] for c in active_cmts} for cid in cg["comment_ids"])
    ]
    return {
        "ok": True,
        "project_id": project_id,
        "version_id": version_id,
        "comments": active_cmts,
        "all_versions_comments": enriched_all if include_all_versions else active_cmts,
        "conflicts": active_conflicts,
    }


@app.post("/api/comments")
def api_create_comment(req: CreateCommentRequest):
    now_str = time.strftime("%Y-%m-%d %H:%M:%S")
    now_ts = int(time.time())
    kind = "suggestion" if req.kind == "suggestion" else "comment"
    valid_scopes = {"word", "phrase", "sentence", "paragraph", "chapter", "page", "global"}
    scope = req.scope if req.scope in valid_scopes else "paragraph"

    cid = f"cmt-{uuid.uuid4().hex[:10]}"
    cmt_obj = {
        "comment_id": cid,
        "project_id": req.project_id,
        "version_id": req.version_id,
        "version_label": req.version_label or "v1.0",
        "doc_id": req.doc_id,
        "kind": kind,
        "scope": scope,
        "page_number": req.page_number,
        "chapter_number": req.chapter_number,
        "unit_index": req.unit_index,
        "unit_mode": req.unit_mode or "paragraph",
        "char_start": req.char_start,
        "char_end": req.char_end,
        "selected_text": req.selected_text or "",
        "context_before": req.context_before or "",
        "context_after": req.context_after or "",
        "content": (req.content or "").strip(),
        "suggested_text": req.suggested_text or "",
        "status": "open",
        "audio_timestamp_sec": req.audio_timestamp_sec,
        "voice_at_creation": req.voice_at_creation or DEFAULT_VOICE,
        "created_at": now_str,
        "created_ts": now_ts,
        "updated_at": now_str,
        "history": [
            {
                "action": "created",
                "timestamp": now_str,
                "version_label": req.version_label or "v1.0",
                "note": f"Created {scope} {kind}",
            }
        ],
    }

    with LIBRARY_LOCK:
        cmts = _load_project_comments_unlocked(req.project_id)
        cmts.append(cmt_obj)
        _save_project_comments_unlocked(req.project_id, cmts)
        proj = _load_project_unlocked(req.project_id)
        if proj:
            _save_project_unlocked(proj)

    enriched, conflicts = compute_comments_overlap_metadata([dict(c) for c in cmts])
    ver_cmts = [c for c in enriched if c.get("version_id") == req.version_id]
    created_enriched = next((c for c in ver_cmts if c["comment_id"] == cid), cmt_obj)
    return {
        "ok": True,
        "autosaved": True,
        "autosaved_at": now_str,
        "comment": created_enriched,
        "comments": ver_cmts,
        "all_versions_comments": enriched,
        "conflicts": conflicts,
    }


@app.patch("/api/comments/{comment_id}")
def api_patch_comment(comment_id: str, req: PatchCommentRequest):
    now_str = time.strftime("%Y-%m-%d %H:%M:%S")
    with LIBRARY_LOCK:
        cmts = _load_project_comments_unlocked(req.project_id)
        target = next((c for c in cmts if c["comment_id"] == comment_id), None)
        if not target:
            raise HTTPException(status_code=404, detail="Comment not found.")

        changes = []
        if req.kind is not None and req.kind in ("comment", "suggestion") and req.kind != target.get("kind"):
            target["kind"] = req.kind
            changes.append(f"switched to {req.kind}")
        if req.scope is not None and req.scope != target.get("scope"):
            target["scope"] = req.scope
            changes.append(f"scope → {req.scope}")
        if req.content is not None and req.content != target.get("content"):
            target["content"] = req.content
            changes.append("edited note")
        if req.suggested_text is not None and req.suggested_text != target.get("suggested_text"):
            target["suggested_text"] = req.suggested_text
            changes.append("updated suggestion replacement")
        if req.status is not None and req.status in ("open", "resolved", "applied", "archived") and req.status != target.get("status"):
            prev_st = target.get("status", "open")
            target["status"] = req.status
            changes.append(f"status {prev_st} → {req.status}")
        if req.char_start is not None:
            target["char_start"] = req.char_start
        if req.char_end is not None:
            target["char_end"] = req.char_end
        if req.selected_text is not None:
            target["selected_text"] = req.selected_text

        target["updated_at"] = now_str
        if changes or req.history_note:
            target.setdefault("history", []).append({
                "action": req.status if (req.status and len(changes) == 1 and "status" in changes[0]) else "edited",
                "timestamp": now_str,
                "version_label": target.get("version_label", "v1.0"),
                "note": req.history_note or (", ".join(changes) if changes else "Autosaved update"),
            })

        _save_project_comments_unlocked(req.project_id, cmts)
        proj = _load_project_unlocked(req.project_id)
        if proj:
            _save_project_unlocked(proj)

    enriched, conflicts = compute_comments_overlap_metadata([dict(c) for c in cmts])
    ver_id = target.get("version_id")
    ver_cmts = [c for c in enriched if c.get("version_id") == ver_id]
    updated_enriched = next((c for c in ver_cmts if c["comment_id"] == comment_id), target)
    return {
        "ok": True,
        "autosaved": True,
        "autosaved_at": now_str,
        "comment": updated_enriched,
        "comments": ver_cmts,
        "all_versions_comments": enriched,
        "conflicts": conflicts,
    }


@app.delete("/api/comments/{comment_id}")
def api_delete_comment(comment_id: str, project_id: str = Query(...)):
    with LIBRARY_LOCK:
        cmts = _load_project_comments_unlocked(project_id)
        target = next((c for c in cmts if c["comment_id"] == comment_id), None)
        if not target:
            raise HTTPException(status_code=404, detail="Comment not found.")
        ver_id = target.get("version_id")
        remaining = [c for c in cmts if c["comment_id"] != comment_id]
        _save_project_comments_unlocked(project_id, remaining)

    enriched, conflicts = compute_comments_overlap_metadata([dict(c) for c in remaining])
    return {
        "ok": True,
        "deleted_comment_id": comment_id,
        "comments": [c for c in enriched if c.get("version_id") == ver_id],
        "all_versions_comments": enriched,
        "conflicts": conflicts,
    }


@app.post("/api/comments/resolve-conflict")
def api_resolve_overlapping_suggestion_conflict(req: ResolveConflictRequest):
    now_str = time.strftime("%Y-%m-%d %H:%M:%S")
    with LIBRARY_LOCK:
        cmts = _load_project_comments_unlocked(req.project_id)
        winner = next((c for c in cmts if c["comment_id"] == req.winning_comment_id), None)
        if not winner:
            raise HTTPException(status_code=404, detail="Winning suggestion not found.")
        winner["status"] = "open"
        winner["updated_at"] = now_str
        winner.setdefault("history", []).append({
            "action": "conflict_winner",
            "timestamp": now_str,
            "version_label": winner.get("version_label", "v1.0"),
            "note": "Selected as primary suggestion in overlapping span conflict",
        })
        for c in cmts:
            if c["comment_id"] in req.competing_comment_ids and c["comment_id"] != req.winning_comment_id:
                c["status"] = "resolved"
                c["updated_at"] = now_str
                c.setdefault("history", []).append({
                    "action": "superseded",
                    "timestamp": now_str,
                    "version_label": c.get("version_label", "v1.0"),
                    "note": f"Resolved in favor of overlapping suggestion {req.winning_comment_id}",
                })
        _save_project_comments_unlocked(req.project_id, cmts)

    enriched, conflicts = compute_comments_overlap_metadata([dict(c) for c in cmts])
    ver_id = winner.get("version_id")
    return {
        "ok": True,
        "comments": [c for c in enriched if c.get("version_id") == ver_id],
        "all_versions_comments": enriched,
        "conflicts": conflicts,
    }


@app.post("/api/comments/export")
def api_export_comments_for_llm(req: ExportCommentsRequest):
    with LIBRARY_LOCK:
        proj = _load_project_unlocked(req.project_id)
        if not proj:
            raise HTTPException(status_code=404, detail="Project not found.")
        vid = req.version_id or proj.get("active_version_id")
        versions = proj.get("versions") or []
        ver = next((v for v in versions if v["version_id"] == vid), versions[-1] if versions else None)
        if not ver:
            raise HTTPException(status_code=404, detail="Project version not found.")
        doc_rec = activate_project_version_into_memory(proj, ver)
        cmts = _load_project_comments_unlocked(req.project_id)

    fmt = req.format if req.format in ("llm_prompt_pack", "structured_sheet", "inline_annotated") else "llm_prompt_pack"
    result = build_llm_comments_export(
        proj=proj,
        ver=ver,
        doc_record=doc_rec,
        comments=cmts,
        export_format=fmt,
        include_resolved=req.include_resolved,
    )
    return {"ok": True, **result}


# ==============================================================================
# GENERATED AUDIO VISIBILITY & MULTI-VOICE UNIT STITCHING ENDPOINTS (v3.0)
# ==============================================================================
@app.get("/api/tts/generated-status/{doc_id}")
def api_get_generated_status(doc_id: str):
    doc_obj = DOCUMENTS_STORE.get(doc_id)
    return get_generated_status_for_doc(doc_id, doc_obj=doc_obj)


@app.post("/api/tts/stitch-units")
def api_stitch_units(req: StitchUnitsRequest):
    """
    Stitches paragraph/line units (even across multiple distinct character/chapter voices!)
    into a unified lossless 24kHz WAV + MP3 stream. If `synthesize_missing=True`, synthesizes
    any uncached paragraphs on the fly with their assigned voice!
    """
    if not req.units:
        raise HTTPException(status_code=400, detail="No units provided to stitch.")

    total_u = len(req.units)
    inter_pause_sec = 0.14 if req.chunk_mode == "line" else 0.28
    pause_arr = np.zeros(int(SAMPLE_RATE * inter_pause_sec), dtype=np.float32)

    audio_parts: List[np.ndarray] = []
    combined_segments: List[Dict[str, Any]] = []
    cumulative_offset_sec = 0.0
    global_seg_index = 0
    key_parts = [req.doc_id or "doc", f"{req.speed:.2f}", req.chunk_mode]

    for idx, u_item in enumerate(req.units):
        check_task_cancelled(req.task_id)
        pct = 8.0 + ((idx + 1) / total_u) * 82.0
        v_id = u_item.voice if u_item.voice in VOICE_MAP else DEFAULT_VOICE
        v_meta = get_voice_color_meta(v_id)

        unit_meta: Optional[Dict[str, Any]] = None
        if u_item.job_id:
            safe_jid = re.sub(r"[^a-f0-9]", "", u_item.job_id.lower())
            wp = EXPORTS_DIR / f"kokoro_{safe_jid}.wav"
            mp = EXPORTS_DIR / f"kokoro_{safe_jid}.json"
            if wp.exists() and mp.exists():
                try:
                    unit_meta = json.loads(mp.read_text(encoding="utf-8"))
                except Exception:
                    unit_meta = None

        if not unit_meta and req.synthesize_missing and u_item.text.strip():
            update_task_progress(
                req.task_id, pct,
                f"Synthesizing Unit {idx + 1} of {total_u} ({v_meta['voice_name']})",
                f"Page {u_item.page_number} · Paragraph #{u_item.unit_index + 1}",
                current_step=idx + 1,
                total_steps=total_u,
            )
            unit_meta = ENGINE.synthesize(
                text=u_item.text,
                paragraphs=[u_item.text] if req.chunk_mode != "line" else None,
                lines=[u_item.text] if req.chunk_mode == "line" else None,
                voice=v_id,
                speed=req.speed,
                blend_voice=req.blend_voice,
                blend_weight=req.blend_weight,
                chunk_mode=req.chunk_mode,
                strip_markdown=req.strip_markdown,
            )
            if req.doc_id:
                record_generated_audio_item(
                    doc_id=req.doc_id,
                    meta=unit_meta,
                    scope="unit",
                    page_number=u_item.page_number,
                    chapter_number=u_item.chapter_number,
                    unit_index=u_item.unit_index,
                    chunk_mode=req.chunk_mode,
                )

        if not unit_meta:
            continue

        safe_jid = re.sub(r"[^a-f0-9]", "", unit_meta["job_id"].lower())
        wav_path = EXPORTS_DIR / f"kokoro_{safe_jid}.wav"
        if not wav_path.exists():
            continue

        key_parts.append(f"p{u_item.page_number}u{u_item.unit_index}_{v_id}_{safe_jid}")
        wav_data, sr = sf.read(str(wav_path), dtype="float32")
        if wav_data.ndim > 1:
            wav_data = wav_data.mean(axis=1)
        u_dur = float(len(wav_data)) / float(sr)

        if audio_parts:
            audio_parts.append(pause_arr)
            cumulative_offset_sec += inter_pause_sec

        audio_parts.append(wav_data)

        for seg in unit_meta.get("segments") or []:
            s_start = round(float(seg.get("start", 0.0)) + cumulative_offset_sec, 3)
            s_end = round(float(seg.get("end", 0.0)) + cumulative_offset_sec, 3)
            shifted_words = [
                {
                    "word": w.get("word", ""),
                    "whitespace": w.get("whitespace", " "),
                    "start": round(float(w.get("start", 0.0)) + cumulative_offset_sec, 3),
                    "end": round(float(w.get("end", 0.0)) + cumulative_offset_sec, 3),
                }
                for w in (seg.get("words") or [])
            ]
            combined_segments.append({
                "index": global_seg_index,
                "page_number": u_item.page_number,
                "page_unit_index": u_item.unit_index,
                "chapter_number": u_item.chapter_number,
                "unit_type": req.chunk_mode,
                "voice": v_id,
                "voice_name": v_meta["voice_name"],
                "voice_color": v_meta["color"],
                "text": seg.get("text", u_item.text),
                "phonemes": seg.get("phonemes", ""),
                "start": s_start,
                "end": s_end,
                "duration": round(max(0.05, s_end - s_start), 3),
                "words": shifted_words,
            })
            global_seg_index += 1

        cumulative_offset_sec += u_dur

    if not audio_parts:
        raise HTTPException(status_code=400, detail="No valid unit audio waveforms available to stitch.")

    full_audio = np.concatenate(audio_parts)
    stitched_id = hashlib.sha1("|".join(key_parts).encode("utf-8")).hexdigest()[:20]
    out_wav = EXPORTS_DIR / f"kokoro_{stitched_id}.wav"
    sf.write(str(out_wav), full_audio, SAMPLE_RATE, subtype="PCM_16")
    total_duration = round(float(len(full_audio)) / float(SAMPLE_RATE), 3)

    primary_voice = req.units[0].voice if req.units else DEFAULT_VOICE
    out_meta = {
        "job_id": stitched_id,
        "voice": primary_voice,
        "speed": req.speed,
        "chunk_mode": req.chunk_mode,
        "strip_markdown": req.strip_markdown,
        "sample_rate": SAMPLE_RATE,
        "duration_sec": total_duration,
        "duration_seconds": total_duration,
        "segments": combined_segments,
        "audio_url": f"/api/audio/{stitched_id}/stream.wav",
        "wav_url": f"/api/audio/{stitched_id}/stream.wav",
        "audio_wav_url": f"/api/audio/{stitched_id}/stream.wav",
        "downloads": {
            "full_mp3": f"/api/audio/{stitched_id}/download?quality=full_mp3",
            "low_mp3": f"/api/audio/{stitched_id}/download?quality=low_mp3",
            "full_wav": f"/api/audio/{stitched_id}/download?quality=full_wav",
        },
    }
    (EXPORTS_DIR / f"kokoro_{stitched_id}.json").write_text(json.dumps(out_meta, indent=2, ensure_ascii=False), encoding="utf-8")

    update_task_progress(
        req.task_id, 100.0,
        f"Mastered {len(combined_segments)} unit(s) ({total_duration:.1f}s)",
        "Multi-voice stitched waveform ready for playback & MP3 export",
        current_step=total_u,
        total_steps=total_u,
        status="completed",
    )
    return out_meta


# ==============================================================================
# RETAINED & COMPATIBLE v2.0 ENDPOINTS (100% ZERO UTILITY REGRESSION)
# ==============================================================================
@app.get("/api/tasks/progress/{task_id}")
def api_get_task_progress(task_id: str):
    with TASKS_LOCK:
        entry = TASKS_STORE.get(task_id)
        if not entry:
            return {
                "task_id": task_id,
                "percent": 4.0,
                "stage": "Starting task…",
                "detail": "Initializing background worker…",
                "current_step": 0,
                "total_steps": 0,
                "status": "running",
                "elapsed_sec": 0.0,
                "eta_sec": None,
            }
        return dict(entry)


@app.post("/api/diff/compare")
def api_diff_compare(req: DiffCompareRequest):
    doc_a = DOCUMENTS_STORE.get(req.doc_a_id) if req.doc_a_id else None
    if not doc_a and req.doc_a_path and not req.doc_a_path.startswith("[Pasted Text:") and not req.doc_a_path.startswith("text://") and not req.doc_a_path.startswith("library://"):
        doc_a = load_document_from_path(req.doc_a_path, task_id=req.task_id)

    doc_b = DOCUMENTS_STORE.get(req.doc_b_id) if req.doc_b_id else None
    if not doc_b and req.doc_b_path and not req.doc_b_path.startswith("[Pasted Text:") and not req.doc_b_path.startswith("text://") and not req.doc_b_path.startswith("library://"):
        doc_b = load_document_from_path(req.doc_b_path, task_id=req.task_id)

    resolved_text_a = req.doc_a_text or req.text_a
    resolved_text_b = req.doc_b_text or req.text_b

    if not doc_a and not (resolved_text_a and resolved_text_a.strip()):
        raise HTTPException(status_code=400, detail="Please load or paste Document A (Original) first.")
    if not doc_b and not (resolved_text_b and resolved_text_b.strip()):
        raise HTTPException(status_code=400, detail="Please load or paste Document B (Revised) first.")

    resolved_granularity = (req.chunk_mode or req.granularity or "paragraph").lower()
    if resolved_granularity not in ("paragraph", "line"):
        resolved_granularity = "paragraph"

    try:
        return compute_intelligent_document_diff(
            doc_a=doc_a,
            doc_b=doc_b,
            text_a=resolved_text_a,
            text_b=resolved_text_b,
            label_a=req.doc_a_title or req.label_a or "Document A (Original)",
            label_b=req.doc_b_title or req.label_b or "Document B (Revised)",
            granularity=resolved_granularity,
            strip_markdown=req.strip_markdown,
            task_id=req.task_id,
        )
    except Exception as e:
        update_task_progress(req.task_id, 100.0, "Diff comparison failed", str(e), status="error")
        raise HTTPException(status_code=500, detail=f"Diff comparison failed: {e}")


@app.post("/api/diff/load-sample")
def api_diff_load_sample(
    granularity: str = Query(default="paragraph"),
    strip_markdown: bool = Query(default=True),
    task_id: Optional[str] = Query(default=None),
):
    update_task_progress(task_id, 6.0, "Preparing sample v1 & v3 PDFs", "Generating Draft A (Original) and Draft B (Revised) PDFs…")
    pdf_a_path, pdf_b_path = ensure_sample_diff_pdfs()
    doc_a = load_document_from_path(str(pdf_a_path), task_id=task_id)
    doc_b = load_document_from_path(str(pdf_b_path), task_id=task_id)
    diff_result = compute_intelligent_document_diff(
        doc_a=doc_a,
        doc_b=doc_b,
        label_a=doc_a["filename"],
        label_b=doc_b["filename"],
        granularity=granularity,
        strip_markdown=strip_markdown,
        task_id=task_id,
    )
    return {**diff_result, "doc_a": doc_a, "doc_b": doc_b, "diff": diff_result}


@app.post("/api/document/load-sample")
def api_load_sample(task_id: Optional[str] = Query(default=None)):
    update_task_progress(task_id, 5.0, "Preparing sample PDF guide", "Checking embedded TOC chapters and pages…")
    sample_path = ensure_sample_pdf()
    return load_document_from_path(str(sample_path), task_id=task_id)


@app.post("/api/document/load-path")
def api_load_path(req: LoadPathRequest):
    resolved_path = (req.path or req.filepath or "").strip()
    if not resolved_path:
        raise HTTPException(status_code=400, detail="Please provide a valid file `path` or `filepath`.")
    try:
        return load_document_from_path(resolved_path, task_id=req.task_id)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to load document: {e}")


@app.post("/api/document/pick-local")
def api_pick_local_file(
    task_id: Optional[str] = Query(default=None),
    auto_link: bool = Query(default=True),
):
    update_task_progress(task_id, 10.0, "Waiting for macOS Finder selection…", "Choose a local PDF, DOCX, RTF, Markdown, or TXT file in Finder…")
    script = (
        'POSIX path of (choose file with prompt '
        '"Select a PDF or Document to Read with Kokoro-82M Studio v3.0:")'
    )
    try:
        res = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=120)
        if res.returncode != 0:
            update_task_progress(task_id, 100.0, "Selection cancelled", "File dialog closed without selecting a file.", status="completed")
            return {"cancelled": True, "detail": "File selection cancelled."}
        chosen_path = res.stdout.strip()
        if not chosen_path:
            update_task_progress(task_id, 100.0, "Selection cancelled", "No file selected.", status="completed")
            return {"cancelled": True, "detail": "No file selected."}
        doc = load_document_from_path(chosen_path, task_id=task_id, auto_link_project=auto_link)
        return {"cancelled": False, "document": doc, **doc}
    except Exception as e:
        update_task_progress(task_id, 100.0, "Finder dialog error", str(e), status="error")
        raise HTTPException(status_code=500, detail=f"macOS file dialog error: {e}")


@app.get("/api/fs/browse")
def api_browse_filesystem(
    path: Optional[str] = Query(default=None),
    dir_path: Optional[str] = Query(default=None),
):
    effective_p = path if (path and path.strip()) else dir_path
    if not effective_p or not effective_p.strip():
        target = Path.home() / "Downloads"
        if not target.exists():
            target = Path.home()
    else:
        target = Path(os.path.expanduser(effective_p.strip())).resolve()

    if not target.exists():
        raise HTTPException(status_code=404, detail=f"Directory not found: {target}")
    if target.is_file():
        target = target.parent

    is_inside_saves = str(target).startswith(str(PROGRESS_SAVES_DIR))
    allowed_exts = {".pdf", ".txt", ".md", ".docx", ".rtf", ".html", ".htm", ".csv"}
    if is_inside_saves:
        allowed_exts = allowed_exts | {".json"}
    entries: List[Dict[str, Any]] = []
    try:
        for item in sorted(target.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
            if item.name.startswith("."):
                continue
            if item.is_dir():
                entries.append({"name": item.name, "path": str(item), "is_dir": True, "ext": "", "size_bytes": 0})
            elif item.suffix.lower() in allowed_exts:
                try:
                    sz = item.stat().st_size
                except Exception:
                    sz = 0
                entries.append({"name": item.name, "path": str(item), "is_dir": False, "ext": item.suffix.lower(), "size_bytes": sz})
    except PermissionError:
        raise HTTPException(status_code=403, detail=f"Permission denied reading folder: {target}")

    raw_roots = [
        {"label": "💾 Auto-Save JSON Folder", "path": str(PROGRESS_SAVES_DIR)},
        {"label": "📁 Studio Root", "path": str(PROJECT_ROOT)},
        {"label": "🏠 Home", "path": str(Path.home())},
        {"label": "📥 Downloads", "path": str(Path.home() / "Downloads")},
        {"label": "📄 Documents", "path": str(Path.home() / "Documents")},
        {"label": "🖥️ Desktop", "path": str(Path.home() / "Desktop")},
        {"label": "📘 Sample PDF Folder", "path": str(ASSETS_DIR)},
    ]
    quick_roots = [r for r in raw_roots if Path(r["path"]).exists()]

    rec_list = get_recent_files_list()
    for r in rec_list:
        r.setdefault("filepath", r.get("path", ""))
        r.setdefault("filename", r.get("name", ""))
        r["exists"] = bool(r.get("filepath") and Path(r["filepath"]).exists())

    parent_str = str(target.parent) if target.parent != target else str(target)
    return {
        "current_path": str(target),
        "current_dir": str(target),
        "parent_path": parent_str,
        "parent_dir": parent_str,
        "quick_roots": quick_roots,
        "recent_files": rec_list,
        "recents": rec_list,
        "entries": entries[:400],
    }


@app.get("/api/fs/recents")
def api_get_recent_files():
    rec_list = get_recent_files_list()
    for r in rec_list:
        r.setdefault("filepath", r.get("path", ""))
        r.setdefault("filename", r.get("name", ""))
        r["exists"] = bool(r.get("filepath") and Path(r["filepath"]).exists())
    return {"recent_files": rec_list, "recents": rec_list}


@app.post("/api/fs/recents/clear")
def api_clear_recent_files():
    clear_recent_files_list()
    return {"ok": True, "recent_files": [], "recents": []}


@app.get("/api/tasks/queue")
def api_get_tasks_queue():
    with TASKS_LOCK:
        items = sorted(TASKS_STORE.values(), key=lambda x: x.get("started_at", 0), reverse=True)[:40]
        running_cnt = sum(1 for x in items if x.get("status") == "running")
        queued_cnt = sum(1 for x in items if x.get("status") == "queued")
    return {
        "active_count": running_cnt + queued_cnt,
        "running_count": running_cnt,
        "queued_count": queued_cnt,
        "tasks": items,
    }


@app.post("/api/tasks/{task_id}/cancel")
def api_cancel_task(task_id: str):
    res = cancel_task_by_id(task_id)
    return {"ok": True, "cancelled": True, "task": res}


@app.post("/api/tasks/cancel-all")
def api_cancel_all_tasks():
    cancelled_count = cancel_all_running_tasks()
    return {"ok": True, "cancelled_count": cancelled_count}


class ClearCacheRequest(BaseModel):
    scope: str = "all"
    filename: Optional[str] = None
    clear_memory_docs: bool = False


def build_cache_status_report() -> Dict[str, Any]:
    items: List[Dict[str, Any]] = []
    audio_bytes = 0
    audio_count = 0
    uploads_bytes = 0
    uploads_count = 0

    if EXPORTS_DIR.exists():
        for f in sorted(EXPORTS_DIR.iterdir(), key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True):
            if not f.is_file() or f.name.startswith("."):
                continue
            try:
                st = f.stat()
            except Exception:
                continue
            ext = f.suffix.lower()
            cat = "audio_wav" if ext == ".wav" else ("audio_mp3" if ext == ".mp3" else "audio_meta")
            desc = "Synthesized 24kHz PCM WAV" if ext == ".wav" else ("Encoded MP3 Export" if ext == ".mp3" else "TTS Segment & Word Timing Metadata")

            if ext == ".wav":
                json_companion = f.with_suffix(".json")
                if json_companion.exists():
                    try:
                        m = json.loads(json_companion.read_text(encoding="utf-8"))
                        v = m.get("voice", DEFAULT_VOICE)
                        dur = m.get("duration_sec", 0)
                        segs = len(m.get("segments") or [])
                        desc = f"Voice: {v} • {dur}s audio • {segs} segment(s)"
                    except Exception:
                        pass

            audio_bytes += st.st_size
            audio_count += 1
            items.append({
                "filename": f.name,
                "folder": "exports",
                "category": cat,
                "description": desc,
                "size_bytes": st.st_size,
                "modified_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(st.st_mtime)),
                "mtime": st.st_mtime,
            })

    if CACHE_DIR.exists():
        for f in sorted(CACHE_DIR.iterdir(), key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True):
            if not f.is_file() or f.name.startswith(".") or f.name == "recent_files.json":
                continue
            try:
                st = f.stat()
            except Exception:
                continue
            uploads_bytes += st.st_size
            uploads_count += 1
            items.append({
                "filename": f.name,
                "folder": "cache",
                "category": "uploaded_doc",
                "description": f"Cached / Uploaded Document ({f.suffix.lower() or 'file'})",
                "size_bytes": st.st_size,
                "modified_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(st.st_mtime)),
                "mtime": st.st_mtime,
            })

    for it in items:
        it.setdefault("name", it.get("filename", ""))
    items.sort(key=lambda x: x["size_bytes"], reverse=True)
    return {
        "total_size_bytes": audio_bytes + uploads_bytes,
        "total_bytes": audio_bytes + uploads_bytes,
        "total_items": audio_count + uploads_count,
        "total_files": audio_count + uploads_count,
        "audio_size_bytes": audio_bytes,
        "audio_bytes": audio_bytes,
        "audio_items_count": audio_count,
        "audio_files": audio_count,
        "uploads_size_bytes": uploads_bytes,
        "upload_bytes": uploads_bytes,
        "uploads_items_count": uploads_count,
        "upload_files": uploads_count,
        "in_memory_docs_count": len(DOCUMENTS_STORE),
        "items": items,
    }


@app.get("/api/cache/status")
def api_get_cache_status():
    return build_cache_status_report()


@app.post("/api/cache/clear")
def api_clear_cache(
    req: Optional[ClearCacheRequest] = Body(default=None),
    scope: Optional[str] = Query(default=None),
):
    freed_bytes = 0
    deleted_count = 0
    eff_scope = (scope or (req.scope if req else None) or "all").lower()
    req_fname = req.filename if req else None
    clear_mem = bool(req.clear_memory_docs) if req else False

    if eff_scope == "item" and req_fname:
        safe_name = Path(req_fname).name
        for folder in (EXPORTS_DIR, CACHE_DIR):
            target = folder / safe_name
            if target.exists() and target.is_file() and target.name != "recent_files.json":
                try:
                    sz = target.stat().st_size
                    target.unlink()
                    freed_bytes += sz
                    deleted_count += 1
                except Exception:
                    pass
    else:
        if eff_scope in ("all", "audio") and EXPORTS_DIR.exists():
            for f in list(EXPORTS_DIR.iterdir()):
                if f.is_file() and not f.name.startswith("."):
                    try:
                        sz = f.stat().st_size
                        f.unlink()
                        freed_bytes += sz
                        deleted_count += 1
                    except Exception:
                        pass
        if eff_scope in ("all", "uploads") and CACHE_DIR.exists():
            for f in list(CACHE_DIR.iterdir()):
                if f.is_file() and not f.name.startswith(".") and f.name != "recent_files.json":
                    try:
                        sz = f.stat().st_size
                        f.unlink()
                        freed_bytes += sz
                        deleted_count += 1
                    except Exception:
                        pass

    if clear_mem:
        DOCUMENTS_STORE.clear()

    prune_missing_generated_manifest_entries()
    report = build_cache_status_report()
    return {
        "ok": True,
        "freed_bytes": freed_bytes,
        "deleted_count": deleted_count,
        "removed_count": deleted_count,
        "status": report,
        "cache": report,
    }


class VerifyProgressRequest(BaseModel):
    filename: str = "document.pdf"
    filepath: Optional[str] = None
    relative_path: Optional[str] = None
    project_id: Optional[str] = None
    version_id: Optional[str] = None
    sha256: Optional[str] = None
    expected_sha256: Optional[str] = None
    size_bytes: Optional[int] = None
    expected_size_bytes: Optional[int] = None
    total_pages: Optional[int] = None
    expected_total_pages: Optional[int] = None
    total_paragraphs: Optional[int] = None
    total_lines: Optional[int] = None
    total_words: Optional[int] = None
    override_path: Optional[str] = None
    override_filepath: Optional[str] = None


@app.post("/api/document/verify-progress")
def api_verify_progress_json(req: VerifyProgressRequest):
    eff_sha = req.sha256 or req.expected_sha256
    eff_size = req.size_bytes if req.size_bytes is not None else req.expected_size_bytes
    eff_pages = req.total_pages if req.total_pages is not None else req.expected_total_pages
    eff_override = req.override_path or req.override_filepath
    req.sha256 = eff_sha
    req.size_bytes = eff_size
    req.total_pages = eff_pages
    req.override_path = eff_override
    candidates: List[Tuple[str, Path]] = []
    if req.override_path:
        candidates.append(("override", Path(os.path.expanduser(req.override_path)).resolve()))
    if req.filepath and not req.filepath.startswith("library://"):
        candidates.append(("original", Path(os.path.expanduser(req.filepath)).resolve()))
    if req.relative_path:
        candidates.append(("workspace_relative", (PROJECT_ROOT.parent.parent / req.relative_path).resolve()))
        candidates.append(("home_relative", (Path.home() / req.relative_path).resolve()))

    safe_fname = Path(req.filename or "document.pdf").name
    candidates.append(("assets", ASSETS_DIR / safe_fname))
    candidates.append(("cache", CACHE_DIR / safe_fname))
    candidates.append(("downloads", Path.home() / "Downloads" / safe_fname))
    candidates.append(("documents", Path.home() / "Documents" / safe_fname))

    for rec in get_recent_files_list():
        r_path = rec.get("path")
        if r_path and (rec.get("name") == safe_fname or (req.sha256 and rec.get("sha256") == req.sha256)):
            candidates.append(("recent", Path(r_path)))

    found_source = None
    found_path: Optional[Path] = None
    for src_kind, cand_path in candidates:
        try:
            if cand_path.exists() and cand_path.is_file():
                found_source = src_kind
                found_path = cand_path
                break
        except Exception:
            continue

    if not found_path:
        # Check if this document belongs to a saved Project Library snapshot (e.g. uploaded/pasted manuscript or library:// URI)
        lib_pid = req.project_id
        lib_vid = req.version_id
        if not lib_pid and req.filepath and req.filepath.startswith("library://"):
            m_lib = re.match(r"^library://([^/]+)/([^/.]+)", req.filepath)
            if m_lib:
                lib_pid, lib_vid = m_lib.group(1), m_lib.group(2)
        if lib_pid:
            with LIBRARY_LOCK:
                proj = _load_project_unlocked(lib_pid)
            if proj:
                versions = proj.get("versions") or []
                target_ver = next((v for v in versions if lib_vid and v.get("version_id") == lib_vid), None)
                if not target_ver and req.sha256:
                    target_ver = next((v for v in versions if v.get("sha256") == req.sha256), None)
                if not target_ver and versions:
                    target_ver = next((v for v in versions if v.get("version_id") == proj.get("active_version_id")), versions[-1])
                if target_ver:
                    snap = _snapshot_file(target_ver["version_id"])
                    if snap.exists():
                        txt = snap.read_text(encoding="utf-8")
                        doc = build_doc_record_from_text(
                            txt,
                            filename=target_ver.get("filename") or req.filename,
                            filepath=target_ver.get("filepath") or f"library://{lib_pid}/{target_ver['version_id']}.md",
                            doc_id_override=target_ver.get("doc_id"),
                        )
                        doc["project_id"] = lib_pid
                        doc["version_id"] = target_ver["version_id"]
                        doc["version_label"] = target_ver.get("version_label", "v1.0")
                        doc["project_theme_id"] = proj.get("theme_id", "system_auto")
                        DOCUMENTS_STORE[doc["doc_id"]] = doc
                        return {
                            "ok": True,
                            "can_load": True,
                            "title": "✓ Project Library Snapshot Verified",
                            "status": "exact_match",
                            "file_status": "exact_match",
                            "was_relocated": False,
                            "original_path": req.filepath,
                            "resolved_path": f"Project Library · {proj.get('title', req.filename)} ({target_ver.get('version_label', 'v1.0')})",
                            "resolved_filepath": f"Project Library · {proj.get('title', req.filename)} ({target_ver.get('version_label', 'v1.0')})",
                            "changes": [],
                            "message": f"Verified manuscript ‘{doc['filename']}’ directly from your Project Library.",
                            "document": doc,
                            "project": proj,
                            "active_version": target_ver,
                        }

        return {
            "ok": False,
            "can_load": False,
            "title": "⚠️ File Moved or Missing",
            "status": "missing_or_moved",
            "file_status": "missing_or_deleted",
            "message": f"File ‘{req.filename}’ was not found at ‘{req.filepath or safe_fname}’. Point to its relocated path below to resume.",
            "original_path": req.filepath,
            "expected_filepath": req.filepath,
            "expected_filename": req.filename,
            "expected_size_bytes": req.size_bytes,
            "expected_sha256": req.sha256,
        }

    doc = load_document_from_path(str(found_path))
    cur_sha = doc.get("sha256", "")
    cur_size = doc.get("size_bytes", 0)
    cur_pages = doc.get("total_pages", 0)
    cur_words = doc.get("total_words", 0)

    hash_mismatch = bool(req.sha256 and cur_sha and req.sha256 != cur_sha)
    size_mismatch = bool(req.size_bytes is not None and req.size_bytes > 0 and cur_size != req.size_bytes)
    pages_mismatch = bool(req.total_pages is not None and req.total_pages > 0 and cur_pages != req.total_pages)

    is_modified = hash_mismatch or size_mismatch or pages_mismatch
    was_relocated = found_source not in ("original", "override") and bool(req.filepath and str(found_path) != str(Path(os.path.expanduser(req.filepath)).resolve()))

    if is_modified:
        changes_desc = []
        if hash_mismatch:
            changes_desc.append("SHA-256 content hash changed")
        if size_mismatch:
            changes_desc.append(f"size changed ({req.size_bytes:,} B → {cur_size:,} B)")
        if pages_mismatch:
            changes_desc.append(f"page count changed ({req.total_pages} → {cur_pages} pages)")
        if req.total_words is not None and req.total_words != cur_words:
            changes_desc.append(f"word count changed ({req.total_words:,} → {cur_words:,} words)")

        return {
            "ok": True,
            "can_load": True,
            "title": "⚠️ File Modified Since Saved Progress",
            "status": "modified",
            "file_status": "modified",
            "was_relocated": was_relocated,
            "original_path": req.filepath,
            "resolved_path": str(found_path),
            "resolved_filepath": str(found_path),
            "changes": changes_desc,
            "message": f"File ‘{doc['filename']}’ was found, but its contents have changed since this progress file was saved ({', '.join(changes_desc)}).",
            "differences": {
                "saved_size_bytes": req.size_bytes,
                "current_size_bytes": cur_size,
                "saved_pages": req.total_pages,
                "current_pages": cur_pages,
                "saved_words": req.total_words,
                "current_words": cur_words,
                "saved_sha256": req.sha256,
                "current_sha256": cur_sha,
            },
            "document": doc,
        }

    if was_relocated:
        return {
            "ok": True,
            "can_load": True,
            "title": "✓ Relocated File Found & SHA-256 Verified",
            "status": "relocated_exact_match",
            "file_status": "relocated_exact_match",
            "was_relocated": True,
            "original_path": req.filepath,
            "resolved_path": str(found_path),
            "resolved_filepath": str(found_path),
            "changes": [],
            "message": f"File ‘{doc['filename']}’ was moved from ‘{req.filepath}’ to ‘{found_path}’, and its SHA-256 content fingerprint matches 100%.",
            "document": doc,
        }

    return {
        "ok": True,
        "can_load": True,
        "title": "✓ Exact SHA-256 Match Verified",
        "status": "exact_match",
        "file_status": "exact_match",
        "was_relocated": False,
        "original_path": req.filepath,
        "resolved_path": str(found_path),
        "resolved_filepath": str(found_path),
        "changes": [],
        "message": f"Verified ‘{doc['filename']}’ — SHA-256 fingerprint and structure match 100%.",
        "document": doc,
    }


@app.post("/api/document/upload")
def api_upload_document(
    file: UploadFile = File(...),
    task_id: Optional[str] = Query(default=None),
):
    safe_name = re.sub(r"[^A-Za-z0-9._ -]", "_", file.filename or "uploaded_document.pdf")
    dest_path = CACHE_DIR / f"{int(time.time())}_{safe_name}"
    update_task_progress(task_id, 8.0, f"Saving uploaded file {safe_name}", "Writing file stream to local cache…")
    dest_path.write_bytes(file.file.read())
    try:
        return load_document_from_path(str(dest_path), task_id=task_id, display_filename=safe_name)
    except Exception as e:
        update_task_progress(task_id, 100.0, "Upload parse error", str(e), status="error")
        raise HTTPException(status_code=400, detail=f"Failed to parse uploaded file: {e}")


@app.get("/api/document/{doc_id}")
def api_get_document(doc_id: str):
    if doc_id not in DOCUMENTS_STORE:
        raise HTTPException(status_code=404, detail="Document not found in session.")
    return DOCUMENTS_STORE[doc_id]


@app.put("/api/document/{doc_id}/page/{page_num}")
def api_update_page_text(doc_id: str, page_num: int, req: UpdatePageTextRequest):
    if doc_id not in DOCUMENTS_STORE:
        raise HTTPException(status_code=404, detail="Document not found.")
    doc = DOCUMENTS_STORE[doc_id]
    if page_num < 1 or page_num > len(doc["pages"]):
        raise HTTPException(status_code=400, detail="Invalid page number.")
    old_page = doc["pages"][page_num - 1]
    paragraphs, lines = extract_text_paragraphs_and_lines(req.text)
    headings = []
    for p_i, p_str in enumerate(paragraphs):
        if is_heading_candidate(p_str):
            headings.append({"paragraph_index": p_i, "title": clean_chapter_title(p_str)})
    updated_page = build_page_dict(
        page_num,
        paragraphs,
        lines,
        has_visual_page=old_page.get("has_visual_page", False),
        headings=headings,
    )
    doc["pages"][page_num - 1] = updated_page
    doc["chapters"] = build_chapters_for_document(doc["pages"])
    doc["total_chapters"] = len(doc["chapters"])
    doc["has_chapters"] = len(doc["chapters"]) > 0
    doc["total_paragraphs"] = sum(p["paragraph_count"] for p in doc["pages"])
    doc["total_lines"] = sum(p["line_count"] for p in doc["pages"])
    doc["total_words"] = sum(p["word_count"] for p in doc["pages"])

    # Autosave updated snapshot if linked to a Project Version
    if doc.get("version_id"):
        with LIBRARY_LOCK:
            _snapshot_file(doc["version_id"]).write_text(_extract_full_text_from_doc(doc), encoding="utf-8")

    return {
        "page": updated_page,
        "chapters": doc["chapters"],
        "total_paragraphs": doc["total_paragraphs"],
        "total_lines": doc["total_lines"],
    }


@app.get("/api/document/{doc_id}/page/{page_num}/image")
def api_get_pdf_page_image(doc_id: str, page_num: int, dpi: int = Query(default=150, ge=72, le=300)):
    if doc_id not in DOCUMENTS_STORE:
        raise HTTPException(status_code=404, detail="Document not found.")
    doc_info = DOCUMENTS_STORE[doc_id]
    if doc_info["extension"] != ".pdf":
        raise HTTPException(status_code=400, detail="Visual page preview is only available for PDF documents.")
    pdf_path = Path(doc_info["filepath"])
    if not pdf_path.exists():
        raise HTTPException(status_code=404, detail="Source PDF file no longer exists on disk.")
    try:
        import pymupdf
        pdf_doc = pymupdf.open(str(pdf_path))
        if page_num < 1 or page_num > len(pdf_doc):
            pdf_doc.close()
            raise HTTPException(status_code=400, detail="Page number out of range.")
        page = pdf_doc[page_num - 1]
        pix = page.get_pixmap(dpi=dpi)
        png_bytes = pix.tobytes("png")
        pdf_doc.close()
        return Response(content=png_bytes, media_type="image/png")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to render PDF page: {e}")


@app.post("/api/tts/synthesize")
def api_synthesize(req: SynthesizeRequest):
    try:
        effective_text = req.text or "\n\n".join(req.paragraphs or req.lines or [])
        meta = ENGINE.synthesize(
            text=effective_text,
            voice=req.voice,
            speed=req.speed,
            blend_voice=req.blend_voice,
            blend_weight=req.blend_weight,
            chunk_mode=req.chunk_mode,
            paragraphs=req.paragraphs,
            lines=req.lines,
            strip_markdown=req.strip_markdown,
            task_id=req.task_id,
            title_hint=req.title_hint,
        )
        if req.doc_id:
            inferred_scope = req.scope
            if not inferred_scope:
                if req.unit_index is not None:
                    inferred_scope = "unit"
                elif req.chapter_number is not None and req.page_number is None:
                    inferred_scope = "chapter"
                elif req.page_number is not None:
                    inferred_scope = "page"
                else:
                    inferred_scope = "custom"
            record_generated_audio_item(
                doc_id=req.doc_id,
                meta=meta,
                scope=inferred_scope,
                page_number=req.page_number,
                chapter_number=req.chapter_number,
                unit_index=req.unit_index,
                chunk_mode=req.chunk_mode,
            )
        return meta
    except TaskCancelledError as e:
        return JSONResponse({"cancelled": True, "detail": str(e)}, status_code=499)
    except ValueError as e:
        update_task_progress(req.task_id, 100.0, "Synthesis validation error", str(e), status="error")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        update_task_progress(req.task_id, 100.0, "Kokoro TTS synthesis error", str(e), status="error")
        raise HTTPException(status_code=500, detail=f"Kokoro TTS synthesis error: {e}")


@app.post("/api/tts/export-document")
def api_export_document(req: ExportDocumentRequest):
    if req.doc_id not in DOCUMENTS_STORE:
        raise HTTPException(status_code=404, detail="Document not found.")
    doc = DOCUMENTS_STORE[req.doc_id]
    total_pages = doc["total_pages"]
    start_p = max(1, min(total_pages, req.start_page))
    end_p = max(start_p, min(total_pages, req.end_page or total_pages))

    update_task_progress(
        req.task_id, 3.0,
        f"Assembling Pages {start_p}–{end_p} of {doc['filename']}",
        f"Collecting {req.chunk_mode}s across {end_p - start_p + 1} page(s) for audiobook mastering…",
    )

    sections: List[str] = []
    all_paras: List[str] = []
    all_lines: List[str] = []
    for p_num in range(start_p, end_p + 1):
        p_obj = doc["pages"][p_num - 1]
        p_text = (p_obj.get("text") or "").strip()
        if not p_text:
            continue
        if req.announce_page_numbers or bool(req.include_page_announcements):
            sections.append(f"Page {p_num}.\n\n{p_text}")
            all_paras.append(f"Page {p_num}.")
            all_lines.append(f"Page {p_num}.")
        else:
            sections.append(p_text)
        all_paras.extend(p_obj.get("paragraphs") or [])
        all_lines.extend(p_obj.get("lines") or [])

    if not sections:
        update_task_progress(req.task_id, 100.0, "Empty page range", "Selected page range contains no readable text.", status="error")
        raise HTTPException(status_code=400, detail="Selected page range contains no readable text.")

    combined_text = "\n\n".join(sections)
    try:
        meta = ENGINE.synthesize(
            text=combined_text,
            voice=req.voice,
            speed=req.speed,
            blend_voice=req.blend_voice,
            blend_weight=req.blend_weight,
            chunk_mode=req.chunk_mode,
            paragraphs=all_paras,
            lines=all_lines,
            strip_markdown=req.strip_markdown,
            task_id=req.task_id,
            title_hint=f"{doc['filename']} (Pages {start_p}-{end_p})",
        )
        meta["exported_pages"] = {"start_page": start_p, "end_page": end_p, "filename": doc["filename"]}
        meta["full_mp3_url"] = f"/api/audio/{meta['job_id']}/download?quality=full_mp3"
        meta["low_mp3_url"] = f"/api/audio/{meta['job_id']}/download?quality=low_mp3"
        return meta
    except TaskCancelledError as e:
        return JSONResponse({"cancelled": True, "detail": str(e)}, status_code=499)
    except Exception as e:
        update_task_progress(req.task_id, 100.0, "Audiobook export failed", str(e), status="error")
        raise HTTPException(status_code=500, detail=f"Document export synthesis failed: {e}")


@app.post("/api/tts/stitch-pages")
def api_stitch_pages(req: StitchPagesRequest):
    if not req.pages and req.page_job_ids:
        built_pages: List[StitchPageItem] = []
        for idx_p, jid in enumerate(req.page_job_ids, start=1):
            safe_j = re.sub(r"[^a-f0-9]", "", jid.lower())
            mp = EXPORTS_DIR / f"kokoro_{safe_j}.json"
            segs = []
            if mp.exists():
                try:
                    segs = json.loads(mp.read_text(encoding="utf-8")).get("segments") or []
                except Exception:
                    segs = []
            built_pages.append(StitchPageItem(page_number=idx_p, job_id=safe_j, segments=segs))
        req.pages = built_pages

    if not req.pages:
        raise HTTPException(status_code=400, detail="No page jobs provided to stitch.")

    update_task_progress(
        req.task_id, 15.0,
        f"Stitching {len(req.pages)} loaded pages into unified document stream…",
        f"Aligning waveforms, chapter markers, and word-level timestamps across {len(req.pages)} pages…",
        current_step=1,
        total_steps=len(req.pages),
    )

    sample_rate = SAMPLE_RATE
    inter_page_pause_sec = 0.38
    pause_arr = np.zeros(int(sample_rate * inter_page_pause_sec), dtype=np.float32)

    audio_parts: List[np.ndarray] = []
    combined_segments: List[Dict[str, Any]] = []
    cumulative_offset_sec = 0.0
    global_seg_index = 0
    v_meta = get_voice_color_meta(req.voice)

    job_key_parts = [req.doc_id or "doc", req.voice, f"{req.speed:.2f}", req.chunk_mode]
    for idx, p_item in enumerate(req.pages):
        safe_jid = re.sub(r"[^a-f0-9]", "", p_item.job_id.lower())
        wav_path = EXPORTS_DIR / f"kokoro_{safe_jid}.wav"
        if not wav_path.exists():
            continue

        job_key_parts.append(f"p{p_item.page_number}_{safe_jid}")
        wav_data, sr = sf.read(str(wav_path), dtype="float32")
        if wav_data.ndim > 1:
            wav_data = wav_data.mean(axis=1)
        page_dur_sec = float(len(wav_data)) / float(sr)

        if audio_parts:
            audio_parts.append(pause_arr)
            cumulative_offset_sec += inter_page_pause_sec

        audio_parts.append(wav_data)

        for local_idx, seg in enumerate(p_item.segments or []):
            s_start = round(float(seg.get("start", 0.0)) + cumulative_offset_sec, 3)
            s_end = round(float(seg.get("end", 0.0)) + cumulative_offset_sec, 3)
            shifted_words = [
                {
                    "word": w.get("word", ""),
                    "whitespace": w.get("whitespace", " "),
                    "start": round(float(w.get("start", 0.0)) + cumulative_offset_sec, 3),
                    "end": round(float(w.get("end", 0.0)) + cumulative_offset_sec, 3),
                }
                for w in (seg.get("words") or [])
            ]
            combined_segments.append({
                "index": global_seg_index,
                "page_number": p_item.page_number,
                "page_unit_index": local_idx,
                "chapter_number": p_item.chapter_number,
                "chapter_title": p_item.chapter_title,
                "unit_type": seg.get("unit_type", req.chunk_mode),
                "voice": seg.get("voice", req.voice),
                "voice_name": seg.get("voice_name", v_meta["voice_name"]),
                "voice_color": seg.get("voice_color", v_meta["color"]),
                "text": seg.get("text", ""),
                "phonemes": seg.get("phonemes", ""),
                "start": s_start,
                "end": s_end,
                "duration": round(max(0.05, s_end - s_start), 3),
                "words": shifted_words,
            })
            global_seg_index += 1

        cumulative_offset_sec += page_dur_sec
        pct = 15.0 + ((idx + 1) / len(req.pages)) * 75.0
        update_task_progress(
            req.task_id, pct,
            f"Stitched Page {p_item.page_number} ({idx + 1} of {len(req.pages)})",
            f"Cumulative audio: {cumulative_offset_sec:.1f}s · {global_seg_index} {req.chunk_mode}s aligned",
            current_step=idx + 1,
            total_steps=len(req.pages),
        )

    if not audio_parts:
        raise HTTPException(status_code=400, detail="No valid page audio files found to stitch.")

    full_audio = np.concatenate(audio_parts)
    stitched_id = hashlib.sha1("|".join(job_key_parts).encode("utf-8")).hexdigest()[:20]
    out_wav_path = EXPORTS_DIR / f"kokoro_{stitched_id}.wav"
    sf.write(str(out_wav_path), full_audio, sample_rate, subtype="PCM_16")
    total_duration = round(float(len(full_audio)) / float(sample_rate), 3)

    update_task_progress(
        req.task_id, 100.0,
        f"All {len(req.pages)} Pages, Chapters & {global_seg_index} {req.chunk_mode.title()}s Loaded!",
        f"Total Master Audio: {total_duration:.1f}s ({len(req.pages)} pages · {global_seg_index} {req.chunk_mode}s)",
        current_step=len(req.pages),
        total_steps=len(req.pages),
        status="completed",
    )

    return {
        "job_id": stitched_id,
        "voice": req.voice,
        "speed": req.speed,
        "chunk_mode": req.chunk_mode,
        "strip_markdown": req.strip_markdown,
        "sample_rate": sample_rate,
        "duration_sec": total_duration,
        "duration_seconds": total_duration,
        "total_pages_stitched": len(req.pages),
        "segments": combined_segments,
        "audio_url": f"/api/audio/{stitched_id}/stream.wav",
        "wav_url": f"/api/audio/{stitched_id}/stream.wav",
        "audio_wav_url": f"/api/audio/{stitched_id}/stream.wav",
        "downloads": {
            "full_mp3": f"/api/audio/{stitched_id}/download?quality=full_mp3",
            "low_mp3": f"/api/audio/{stitched_id}/download?quality=low_mp3",
            "full_wav": f"/api/audio/{stitched_id}/download?quality=full_wav",
        },
        "download_full_mp3_url": f"/api/audio/{stitched_id}/download?quality=full_mp3",
        "download_low_mp3_url": f"/api/audio/{stitched_id}/download?quality=low_mp3",
        "download_wav_url": f"/api/audio/{stitched_id}/download?quality=full_wav",
    }


@app.post("/api/audio/{job_id}/prepare")
def api_prepare_audio_download(
    job_id: str,
    req: Optional[PrepareAudioRequest] = Body(default=None),
    quality: Optional[str] = Query(default=None),
    filename: Optional[str] = Query(default=None),
    task_id: Optional[str] = Query(default=None),
):
    eff_quality = quality or (req.quality if req else None) or "full_mp3"
    eff_filename = filename or (req.filename if req else None)
    eff_task_id = task_id or (req.task_id if req else None)

    safe_id = re.sub(r"[^a-f0-9]", "", job_id.lower())
    wav_path = EXPORTS_DIR / f"kokoro_{safe_id}.wav"
    if not wav_path.exists():
        update_task_progress(eff_task_id, 100.0, "Audio file not found", "Master WAV file is missing.", status="error")
        raise HTTPException(status_code=404, detail="Synthesized audio not found.")

    q_norm = eff_quality if eff_quality in ("full_mp3", "low_mp3", "full_wav") else "full_mp3"
    base_label = re.sub(r"[^A-Za-z0-9._-]", "_", (eff_filename or f"Kokoro_{safe_id[:8]}").strip())
    base_label = re.sub(r"\.(pdf|txt|md|docx|wav|mp3)$", "", base_label, flags=re.IGNORECASE)

    update_task_progress(
        eff_task_id, 10.0,
        f"Preparing {q_norm.upper()} Audio Export",
        f"Source waveform: {wav_path.name} ({wav_path.stat().st_size / 1024:.0f} KB)",
        current_step=1,
        total_steps=2,
    )

    try:
        out_path = convert_wav_to_mp3(wav_path, quality=q_norm, task_id=eff_task_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    if q_norm == "full_wav":
        dl_name = f"{base_label}_FullRes_24kHz.wav"
    elif q_norm == "full_mp3":
        dl_name = f"{base_label}_FullRes_320kbps.mp3"
    else:
        dl_name = f"{base_label}_LoQuality_48kbps.mp3"

    size_bytes = out_path.stat().st_size if out_path.exists() else 0
    return {
        "ok": True,
        "job_id": safe_id,
        "quality": q_norm,
        "filename": dl_name,
        "size_bytes": size_bytes,
        "download_url": f"/api/audio/{safe_id}/download?quality={q_norm}&filename={base_label}",
    }


@app.get("/api/audio/{job_id}/stream.wav")
def api_stream_wav(job_id: str):
    safe_id = re.sub(r"[^a-f0-9]", "", job_id.lower())
    wav_path = EXPORTS_DIR / f"kokoro_{safe_id}.wav"
    if not wav_path.exists():
        raise HTTPException(status_code=404, detail="Audio file not found.")
    return FileResponse(str(wav_path), media_type="audio/wav")


@app.get("/api/audio/{job_id}/download")
def api_download_audio(
    job_id: str,
    quality: str = Query(default="full_mp3", pattern="^(full_mp3|low_mp3|full_wav)$"),
    filename: Optional[str] = Query(default=None),
    task_id: Optional[str] = Query(default=None),
):
    safe_id = re.sub(r"[^a-f0-9]", "", job_id.lower())
    wav_path = EXPORTS_DIR / f"kokoro_{safe_id}.wav"
    if not wav_path.exists():
        raise HTTPException(status_code=404, detail="Synthesized audio not found.")

    base_label = re.sub(r"[^A-Za-z0-9._-]", "_", (filename or f"Kokoro_{safe_id[:8]}").strip())
    base_label = re.sub(r"\.(pdf|txt|md|docx|wav|mp3)$", "", base_label, flags=re.IGNORECASE)

    if quality == "full_wav":
        dl_name = f"{base_label}_FullRes_24kHz.wav"
        return FileResponse(str(wav_path), media_type="audio/wav", filename=dl_name)

    try:
        mp3_path = convert_wav_to_mp3(wav_path, quality=quality, task_id=task_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    dl_name = f"{base_label}_FullRes_320kbps.mp3" if quality == "full_mp3" else f"{base_label}_LoQuality_48kbps.mp3"
    return FileResponse(str(mp3_path), media_type="audio/mpeg", filename=dl_name)


def main():
    parser = argparse.ArgumentParser(description="Kokoro-82M Document & PDF Reader Studio Server v3.3")
    parser.add_argument("--host", default="127.0.0.1", help="Host interface (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "7880")), help="Port (default: 7880)")
    parser.add_argument("--preload", action="store_true", help="Preload Kokoro-82M model on startup")
    args = parser.parse_args()

    ensure_sample_pdf()
    load_custom_themes()
    get_autosave_settings_and_stats()
    ensure_seed_library_showcase()
    repair_existing_library_projects_on_boot()

    if args.preload:
        def _bg_preload():
            try:
                ENGINE.get_pipeline("a")
            except Exception as exc:
                print(f"[Kokoro] Preload warning: {exc}")
        threading.Thread(target=_bg_preload, daemon=True).start()

    print("============================================================")
    print(" 🎙️ Kokoro-82M Document & PDF Reader Studio (v3.3)")
    print(f" 🔗 Studio v3.3 URL  : http://{args.host}:{args.port}")
    print(f" 🔗 Studio v3.2 URL  : http://{args.host}:{args.port}/v3.2")
    print(f" 🔗 Studio v3.1 URL  : http://{args.host}:{args.port}/v3.1")
    print(f" 🔗 Studio v3.0 URL  : http://{args.host}:{args.port}/v3")
    print(f" 🔗 Studio v2.0 URL  : http://{args.host}:{args.port}/v2")
    print(f" 🔗 Classic v1.0 URL : http://{args.host}:{args.port}/v1")
    print(f" 💾 Auto-Save Store  : {PROGRESS_SAVES_DIR}")
    print(f" 🗣️ Default Voice    : Aoede ({DEFAULT_VOICE})")
    print(f" 🧠 Model            : https://huggingface.co/{MODEL_REPO_ID}")
    print("============================================================")
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
