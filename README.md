# Reader Studio (`v3.3`)

**Reader Studio** is a local-first neural document, manuscript, and PDF reader studio with real-time 60fps word-level karaoke alignment, multi-version project management, editorial annotations, differential redline inspection, and immersive book typography.

Currently powered by [`hexgrad/Kokoro-82M`](https://huggingface.co/hexgrad/Kokoro-82M) with a modular TTS engine layer designed to support additional local voice models cleanly over time.

---

## Portable One-Click Mac Bundle (Zero Prerequisites)

Want to send **Reader Studio** to another Mac without installing Python, Homebrew, FFmpeg, or eSpeak NG manually?

1. Download **`Reader-Studio-v3.3-Mac-Portable.zip`** (and optionally **`Reader-Studio-v3.3-Features-Guide.pdf`**) from the [v3.3 GitHub Release](https://github.com/se7enxt/Reader-Studio/releases/tag/v3.3).
2. Unzip the archive anywhere (for example `Downloads` or `Applications`).
3. Double-click **`Start Reader Studio.command`** *(first launch only: **Right-Click → Open → Open** to approve macOS Gatekeeper)*.
   - Automatically installs standalone Python 3.12 (`uv`), all required packages, and bundled `espeakng-loader` + `imageio-ffmpeg` audio tools into an isolated local `.venv`, downloads `Kokoro-82M` weights once, and opens **http://127.0.0.1:7880/** in Google Chrome (or your default browser if Chrome isn't installed).
4. Use **`Stop Reader Studio.command`** (or press `Ctrl + C`) whenever you want to stop the local server.

A readable 3-page visual **Features & Quick-Start Guide PDF** is included inside the ZIP root (`Reader Studio — Quick Start & Features Guide.pdf`) and in [`02_DOCS/KOKORO_DOC_FeaturesGuide_v3.3_20261120_FINAL.pdf`](02_DOCS/KOKORO_DOC_FeaturesGuide_v3.3_20261120_FINAL.pdf).

---

## Core Capabilities

- **Fast-Start Paragraph Streaming & Lookahead Caching**  
  Synthesizes and begins playing the active paragraph first while pre-caching upcoming paragraphs quietly in the background. Defaults to **Aoede (`af_aoede`)** with a deterministic **54-voice color-coordinated cast** across multiple languages plus custom 2-voice blending.
- **Drift-Free 60fps Word Highlighting & Click-Any-Word Playback**  
  Monotonic sequence alignment keeps word highlighting locked smoothly across Split PDF/Text, Full Document, Plain Text Studio, Differential Reader, and Book Preview modes. Click any word anywhere to begin speaking immediately from that exact word.
- **Per-Document Automatic `.JSON` Checkpoints (Default: Last 100 Backups)**  
  Continuously tracks your reading position down to the exact word and enclosing sentence inside per-document folders (`03_ASSETS/library/progress-saves/`), saving checkpoints on pauses, word seeks, page/chapter navigation, manual `💾 .JSON` saves, and tab close—with configurable backup retention (`1`–`1000`, default `100`) and 1-click file-manager access.
- **7-Scope Editorial Comments & Wording Suggestions (`C` / `S`, `1`–`7`)**  
  Annotate a specific word, phrase, sentence, paragraph, chapter, page, or global document mid-listen with automatic overlap/conflict detection, structured LLM revision export, and semantic version bumping (`v1.0 → v1.1 → v2.0`).
- **Differential Redline Reader**  
  Compare any two documents or manuscript versions with inline word-level insertion/deletion redline strips and spoken change cues.
- **Immersive Book Preview (`P` / `Esc`)**  
  Distraction-free fine-press typography styled with your active project's theme, three auto-scroll modes (Smooth, Line-by-Line, Paragraph), and a 1-second auto-hiding mouse HUD (`A−` / `A+` font size and `💾 .JSON`).
- **21 Global Persona Themes + 4 Custom Theme Slots**  
  Typography, light/dark opaque surface contrast, border geometry, and paper/ruled/dot canvas textures persisted automatically per project.

---

## Prerequisites

- **Python** `3.10+`
- **FFmpeg** (for MP3 audio exports)
- **eSpeak NG** (for Kokoro phonemization)

**macOS (Homebrew):**
```bash
brew install ffmpeg espeak-ng
```

**Ubuntu / Debian:**
```bash
sudo apt-get update && sudo apt-get install -y ffmpeg espeak-ng
```

---

## Quick Start

```bash
git clone https://github.com/se7enxt/Reader-Studio.git
cd Reader-Studio
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python3 run.py
```

Then open **http://127.0.0.1:7880/** in Google Chrome (or your preferred browser).

- Custom port or deferred model loading:
  ```bash
  python3 run.py --port 7890 --no-preload
  ```
- macOS double-click launcher: `04_SCRIPTS/KOKORO_CODE_Launcher_v3.3_20261116_FINAL.command`
- Detailed feature guide: [`02_DOCS/KOKORO_DOC_UserGuide_v3.3_20261116_FINAL.md`](02_DOCS/KOKORO_DOC_UserGuide_v3.3_20261116_FINAL.md)

---

## Keyboard Shortcuts

| Shortcut | Action |
| :--- | :--- |
| `Click Any Word` | Play immediately from that exact clicked word (in Studio or Book Preview) |
| `Spacebar` | Play / Pause audio (works everywhere except inside text/comment boxes) |
| `Shift + ←` / `Shift + →` | Jump to Previous / Next Paragraph, Line, or Chapter across page boundaries |
| `p` | Toggle Immersive Book Preview mode (`p` or `Esc` to exit) |
| `c` / `s` | Open Comment (`c`) or Wording Suggestion (`s`) anchored to spoken word/sentence or selection |
| `1` – `7` *(in Composer)* | Switch annotation scope: `1` Word · `2` Phrase · `3` Sentence · `4` Paragraph · `5` Chapter · `6` Page · `7` Global |
| `Enter` *(in Composer)* | Autosave note & resume narration (`Shift + Enter` for newline · `Esc` to cancel) |

---

## Privacy & Local Data Storage

All TTS synthesis, document parsing, library metadata, editorial comments, auto-saved `.JSON` checkpoints, and audio exports run **100% locally** on your machine. On first launch, runtime directories (`03_ASSETS/` and `04_AUDIO/`) are created locally and are excluded from version control via `.gitignore` so personal documents and reading histories are never committed.

---

Copyright © se7enxt. All rights reserved.
