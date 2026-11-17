# Kokoro-82M Document & PDF Reader Studio — v3.3 User Guide

## Overview
**Kokoro Reader Studio v3.3** upgrades `.JSON` reading progress persistence from manual browser downloads into an automatic, per-document **Library Checkpoint & Rolling Backup System** (default: **100** auto-saved backup versions per file), while keeping `/v3.2`, `/v3.1`, `/v3`, `/v2`, and `/v1` completely preserved.

---

## What's New in v3.3

### 1. Per-Document Automatic `.JSON` Progress Saving
- Every document in your **Project Library** automatically maintains its own dedicated folder inside:
  `PROJECTS/KOKORO/03_ASSETS/library/progress-saves/doc-<title>-<id>/`
- **`latest-progress.json`** updates continuously as reading and 60fps spoken word alignment advance.
- **Smart Checkpoint History (`save-*.json`)**: Rolling historical `.JSON` checkpoints are automatically recorded on:
  - **Pause** (`Spacebar` or transport pause)
  - **Word-Click Seek** (clicking any word in Studio or `P` Book Preview)
  - **Paragraph / Page / Chapter Advances** (throttled ~12s during non-stop continuous narration; settled 1.2s on rapid page/chapter jumps)
  - **Manual `💾 .JSON` Save** (in the top ribbon or `P` Book Preview HUD — always pins a fresh `manual` checkpoint immediately)
  - **Document Switch & Browser Tab Close** (flushed via `navigator.sendBeacon`)

### 2. Configurable Backup Version Limit in `⚙️ Settings` (Default: `100`)
- Open **⚙️ Settings → Per-Document `.JSON` Auto-Save & Version Backups**.
- Set **Max Backups** anywhere from `1` to `1000` (default **`100`**), or use 1-click presets (`25`, `50`, `100`, `200`, `500`).
- Older auto-saves beyond your configured limit are automatically pruned per document.

### 3. Direct-to-Library `💾 .JSON` Button (Zero Downloads Clutter)
- Clicking **`💾 .JSON`** in the top Controls Ribbon or inside the **`P` Book Preview** floating HUD now saves directly into that document's Library `.JSON` store on disk instead of triggering browser downloads into `~/Downloads`.
- Need a standalone external file copy? Open **`🕒 Checkpoints`** on any document and click **`⬇ Export Copy`** (or **`📂 Reveal`** in Finder).

### 4. Pick Up From Any Point in `🗂️ Library` & Reader
- Each card in **🗂️ Library** displays its **Latest Auto-Saved Point** (`Page · ¶ · Sentence · “word”` + sentence quote) and a **`🕒 Pick Up (N)`** button.
- Clicking **`🕒 Pick Up (N)`** (or clicking the **`✓ Autosaved · N/100`** / **`🕒 N`** pills in the Reader) opens the **Document Auto-Save Checkpoints Picker**, where you can filter by **All Saves**, **Manual (`.JSON`)**, **Pauses & Word Seeks**, or **Auto Progress**, search by page/word/quote, and click **`▶ Pick Up Here`** to jump right back to that exact sentence or word.

### 5. 1-Click macOS Finder Folder Access
- Click **`📂 Open in Finder`** (or the clickable folder path) inside **`⚙️ Settings`**, click **`📂 Auto-Save JSON Folder`** in the **`🗂️ Library`** header, or click **`📂`** on any individual Library project card (or **`📂 Reveal`** on any checkpoint) to open the exact `.JSON` storage folder or highlight the file directly in **macOS Finder**.

---

## Preserved Routes
- **v3.3 Studio (Default)**: `http://127.0.0.1:7880/`
- **v3.2 Studio**: `http://127.0.0.1:7880/v3.2`
- **v3.1 Studio**: `http://127.0.0.1:7880/v3.1`
- **v3.0 Studio**: `http://127.0.0.1:7880/v3`
- **v2.0 Studio**: `http://127.0.0.1:7880/v2`
- **v1.0 Studio**: `http://127.0.0.1:7880/v1`
