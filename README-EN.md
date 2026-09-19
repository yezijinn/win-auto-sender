# WindowsLoopSend — Scheduled Auto Sender

A lightweight, non-intrusive automation utility for Windows. No extensions or plugins needed. It forcibly brings a target window to the foreground via low-level APIs, then injects text through the clipboard combined with simulated keyboard/mouse events, to automatically paste a prompt and press Enter into a target window (OpenCode, VS Code, Windows Terminal, CMD, browsers, etc.) on a schedule.

---

## Core Features

* **Crosshair Drag-To-Aim (Spy++ style)**: press and hold the crosshair, drag it onto the target input box and release to auto-capture the target window handle and its physical screen coordinates.
* **Bypass Focus-Stealing Protection**: uses `AttachThreadInput` and keyboard-state simulation to overcome Windows' background-focus restrictions, forcefully waking and foregrounding the target window.
* **High-DPI Awareness**: natively calls `SetProcessDpiAwareness` to avoid misplaced clicks on 2K/4K screens or at Windows scaling (125%, 150%, etc.).
* **Clipboard Atomic Input**: pastes through the system clipboard + `Ctrl+V`, solving dropped characters, punctuation corruption, and IME interference that plague per-key simulation of long text.
* **Multiple Prompt Rotation**: pre-fill *n* prompts (each card is an independent multi-line message) with two strategies — **Sequential loop** (send in order, then repeat) and **Random** (pick one each time without omission).
* **[Preface]**: optional text prepended to the start of every sent message.
* **[Suffix]**: optional text sent a configurable delay after the main message (in loop mode the delay is capped below the send interval to avoid overlap).
* **Single / Loop Scheduling**:
  - **Single**: fire once at a specified date/time, then stop automatically.
  - **Loop**: set an interval in minutes, optionally fire the first message immediately, with configurable start/end period protection.
* **Real-time clock & trigger preview**: shows the current time and recomputes the future-trigger list on demand.
* **Multi-instance isolation**: click **“New Window”** to open an independent instance for another target window; each instance keeps its own config file, prompt list, preface/suffix, schedule and target.
* **Config management**: auto-save with 400 ms debounce + hot-reload, plus **Save / Import / Restore-to-factory** buttons; config lives in the same folder as the executable (`config-<name>-win-auto-sender.ini`).
* **Date-based versioning**: version code equals the build date (`YYYYMMDD`); releases use pure-numeric tags, with update check against GitHub (primary) / Gitee (fallback).

---

## Requirements

* **OS**: Windows 10 / Windows 11 (x64)
* **Runtime**: a prebuilt `dist/WindowsLoopSend.exe` needs no Python; to run from source, Python 3.8+

---

## Quick Start

### 1. Clone & install dependencies

```bash
git clone https://github.com/yezijinn/win-auto-sender.git
cd win-auto-sender
pip install PySide6 pywin32 pyperclip
```

### 2. Run

> **Important (UAC elevation)**: if the target window runs as Administrator (e.g. an elevated Windows Terminal / VS Code / CMD), this tool **must also run as Administrator**, otherwise Windows UIPI silently blocks all key/mouse messages.

```bash
python win-auto-sender.py
```

Or simply double-click `dist/WindowsLoopSend.exe`.

---

## Usage

1. **Bind window & locate the editor**: press and hold the **crosshair** button, drag to the target's text input and release — the window is bound and coordinates are filled in automatically. (Alternatively pick a visible window from the dropdown.)
2. **Enter prompts**: fill each prompt card (multi-line supported), add with **➕**, remove with **✕**, choose **sequential loop** or **random**.
3. **Set trigger**: single time / loop interval (minutes) + immediate-first option; optional start and end dates.
4. **Test & run**: click **“⚡ Test Send”** first to confirm the window comes to the foreground and Enter is sent, then **“▶ Start”** to keep it running.

---

## Multi-Window

Click **“🆕 New Window (for another target)”** to spawn a fully independent instance (separate process + separate config file). Each instance manages its own target, prompts, preface/suffix, schedule and config. Type the instance name (if any) on startup via `--name <name>` to select a dedicated config file.

---

## Configuration

- Config file: `config-win-auto-sender.ini` (in the same folder as the script/exe).
- Any UI change auto-saves with a 400 ms debounce; editing the file externally hot-syncs back to the UI.
- Toolbar: **Save now**, **Import config** (from a `.ini`), **Restore to factory defaults** (confirm required, blocked while a task is running).

---

## Build

A one-file executable is produced with PyInstaller (no runtime to install on target machines). Use the provided script:

```bash
python build.py
```

Output: `dist/WindowsLoopSend.exe`. The spec file `WindowsLoopSend.spec` pins the entry point and options.

---

## Project Structure

```text
win-auto-sender/
├── win-auto-sender.py    # Entry: GUI, window focusing, clipboard injection, scheduler
├── build.py              # One-command PyInstaller packaging
├── WindowsLoopSend.spec  # PyInstaller spec
├── README.md / README-EN.md
└── .gitignore
```

---

## Dependencies

```text
PySide6>=6.5.0
pywin32>=306
pyperclip>=1.8.2
```

---

## FAQ

* **Q: Log says “sent” but nothing appears in the target window?**
  **A**: The target process likely has higher privileges (e.g. an elevated terminal). Exit and relaunch this tool **as Administrator**.

* **Q: The window pops up on test but the cursor is not in the input box?**
  **A**: Re-drag the crosshair onto the center of the input box and make sure the `X`/`Y` coordinates are non-zero. The scheduler clicks that physical coordinate after waking the window to focus the input.

* **Q: Does firing interfere with what I'm doing?**
  **A**: It brings the target window to the foreground, focuses it and simulates key/mouse input at trigger time (~0.3–0.5 s.) before returning to the background. Optionally the mouse/keyboard are blocked for ~1 s before each send to avoid conflicts.

---

## License

[MIT License](LICENSE).