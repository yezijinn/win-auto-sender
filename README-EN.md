<p align="center">
  <img src="docs/main-page.png" alt="WindowsLoopSend main UI" width="760"/>
</p>

# 🚀 WindowsLoopSend · Scheduled Auto Sender

> A lightweight, non-intrusive Windows automation tool that automatically pastes a prompt and presses Enter into a target window on a schedule — zero plugins, zero intrusion.

<p align="center">
  <b>English</b> ·
  <a href="README.md"><strong>🌏 中文</strong></a>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Platform-Windows_10%2F11-0078d4" alt="Platform"/>
  <img src="https://img.shields.io/badge/GUI-PySide6-blue" alt="GUI"/>
  <img src="https://img.shields.io/badge/Build-Single_File_EXE-brightgreen" alt="Build"/>
  <img src="https://img.shields.io/badge/License-MIT-yellow" alt="License"/>
</p>

Works against targets such as OpenCode, VS Code, Windows Terminal, CMD and browsers: it grabs window focus via low-level APIs, injects text through the clipboard plus simulated key/mouse events, and fires the prompt automatically on schedule. Open multiple windows to drive several targets at once.

---

## ✨ Core Features

| Capability | Description |
|---|---|
| 🎯 **Crosshair Drag-To-Aim** | Spy++-style: drag the crosshair onto the target input box and release to bind the window and append that point as a click step |
| 🖱️ **Click Sequence** | Multiple click points per target, executed in order: custom coordinates (negative coordinates for secondary monitors supported), reorderable (drag / ↑↓) and a **per-step delay in ms** |
| ⌨️ **Key Sequence** | Keys sent after pasting: `Enter` / `Tab` / `Esc` / `F1`–`F12` / `Ctrl+A` and other single or combined keys, each with its **own delay** |
| 📋 **Paste Switch** | Turn the `Ctrl+V` paste off to send only keystrokes (e.g. shortcut-only actions) |
| 🧲 **Bypass Focus-Stealing Protection** | `AttachThreadInput` + keyboard-state simulation to overcome background-focus restrictions |
| 🖥️ **High-DPI Aware** | Native `SetProcessDpiAwareness` — coordinates stay correct at 2K/4K and 125%/150% scaling |
| 📋 **Clipboard Atomic Input** | `Ctrl+V` pasting — no dropped chars, corrupted punctuation, or IME interference |
| 🗂️ **Master-Detail Content Workspace** | Prompt list on the left, editor on the right: add / duplicate / delete / drag-to-reorder — **list order is send order** — plus one-click `.txt/.md` import |
| 🔁 **Multiple Prompt Rotation** | **Sequential loop** or **pure random** sending strategies |
| 📝 **[Preface]** | Optional text (collapsible section) prepended to the start of every sent message |
| ⏱️ **[Suffix]** | Optional text (collapsible section) sent N seconds after the main message; in loop mode the delay is capped below the interval |
| ⏰ **Single / Loop Scheduling** | Single fires once then stops; loop sets a minute interval, optional immediate first fire, date-range protection and a live preview of upcoming fire times |
| 🧩 **Resizable Three-Pane Layout** | Left: target / schedule / send behaviour · Right: content · Bottom: run log — splitters are draggable, the log collapses, window size and pane ratios are remembered |
| 🪟 **Multi-instance Isolation** | One-click new window manages another target; each instance keeps its own config/content/schedule |
| 💾 **Config Management** | Auto-save (400 ms debounce) + hot-reload + manual save / import / restore-to-factory, with a live save-state indicator |
| 🔒 **Lock Input Before Send** | Switch under “send behaviour”: blocks mouse/keyboard ~1 s before each send to avoid conflicts (needs admin rights; silently skipped otherwise) |

---

## 🚀 Quick Start

### 1. Clone & install dependencies

```bash
git clone https://github.com/yezijinn/win-auto-sender.git
cd win-auto-sender
pip install PySide6 pywin32 pyperclip
```

### 2. Run

> **⚠️ Important (UAC)**: if the target window runs as **Administrator** (e.g. an elevated Terminal / VS Code / CMD), this tool **must also run as Administrator**, otherwise Windows UIPI silently blocks key/mouse messages.

```bash
python win-auto-sender.py
```

Or simply double-click `dist\WindowsLoopSend.exe` (single file; no Python needed on the target machine).

---

## 🖱️ Layout

```text
┌─ Toolbar: status · clock · countdown · save/import/restore/new window ────────┐
│ ┌─ Left: settings ────────────┐ ┌─ Right: content ────────────────────┐ │
│ │ 🎯 Target + click sequence (points/order/delay) │ │ Toolbar: add/dup/del/move/import + strategy │ │
│ │ ⏰ Schedule (single/loop + fire preview)   │ │ Prompt list ｜ editor (master-detail)       │ │
│ │ ⚙️ Send behaviour (paste switch, lock input, key sequence) │ │ Preface / Suffix (collapsible)   │ │
│ └────────────────────────────┘ └────────────────────────────────────┘ │
│ ▾ Run log (collapsible, draggable height)                                   │
│ Action bar: [▶ Start] [⚡ Test Send]                        save-state hint   │
└────────────────────────────────────────────────────────────────────────────┘
```

All splitters, the log height and the window size can be dragged and are restored on the next launch.

## 🖱️ Usage

1. **Bind window and click points**: press and hold the **🎯 crosshair** in the left pane, drag to the target input area and release — the window is bound and that point is **appended** as a click step. Repeat to click several places (input box → send button), then reorder with **↑/↓** and set a **delay (ms)** per step. With no click point at all the tool only foregrounds the window.
2. **Enter prompts**: click **➕ Add** in the right-hand content pane, then write in the editor (multi-line supported); reorder with **⬆/⬇** or by dragging the list items; optionally fill **[Preface]** / **[Suffix]**.
3. **Set trigger**: single fires at a chosen time; loop sets a minute interval, optional immediate first fire and start/end dates, with a live preview of the next fire times.
4. **Configure send behaviour**: decide whether to **paste the content (Ctrl+V)** and edit the **key sequence** — one `Enter` by default, replaceable with `Tab`, `F5`, `Ctrl+A` and other single or combined keys, each with its own delay.
5. **Test, then run**: click **⚡ Test Send** first to confirm focus, clicks, paste and keys all land correctly, then **▶ Start** to keep it running.

---

## 📥 Import Prompts

Click **📂 Import** in the content workspace to load `发送内容.txt` / `发送内容.md` in one click — the app **auto-splits by `---`** into independent prompts (missing markers at the start/end are handled intelligently) which you can then edit and reorder in the list. You can also load a `.ini` config via the **📥 Import Config** button.

<p align="center">
  <img src="docs/导入文本列表.png" alt="Import prompt list" width="640"/>
</p>

---

## 🪟 Multi-Window / Multi-Target

Click **“🆕 New Window”** in the toolbar to open a fully independent instance — **separate process + separate config file** (`config-<name>-win-auto-sender.ini`). Prompts, preface/suffix, schedule and target are all independent per instance.

<p align="center">
  <img src="docs/双开程序.png" alt="Multi-window example" width="760"/>
</p>

> On startup you may use `--name <name>` to select a dedicated config. A brand-new window that was **never modified is cleaned up silently on close** — no leftover files, no annoying prompts.

---

## 💾 Config Management

- Config file: `config-win-auto-sender.ini` (same folder as the exe).
- UI changes auto-save with a **400 ms debounce**; editing the file externally **hot-syncs** back to the UI; the bottom bar shows a live “unsaved / saved at” state.
- Toolbar provides **💾 Save / 📥 Import / ♻️ Restore-to-factory**; import & restore are blocked while a task is running.
- Window size, pane ratios and log visibility are remembered separately — they never pollute the ini config.

<p align="center">
  <img src="docs/关闭程序.png" alt="Close-window config recycle prompt" width="560"/>
</p>

---

## 🔧 Build

Single-file PyInstaller build — no runtime needed on target machines:

```bash
python build.py
# Output: dist\WindowsLoopSend.exe
```

---

## 📁 Project Structure

```text
win-auto-sender/
├── win-auto-sender.py    # Entry: GUI, focus-grab, clipboard injection, scheduler
├── build.py              # One-command PyInstaller packaging
├── WindowsLoopSend.spec  # PyInstaller spec
├── docs/                 # UI screenshots
├── README.md / README-EN.md
└── .gitignore
```

## 📦 Dependencies

```text
PySide6>=6.5.0
pywin32>=306
pyperclip>=1.8.2
```

---

## 💬 FAQ

**Q: Log says “sent” but nothing appears in the target window?**
A: The target process likely runs at a higher privilege (e.g. an elevated terminal). Quit and relaunch with **right-click → Run as administrator**.

**Q: The window pops up on test but the cursor is not in the input box?**
A: Re-drag the crosshair onto the center of the input box and make sure `X/Y` coordinates are non-zero.

**Q: Does firing interfere with what I'm doing?**
A: It foregrounds, focuses and simulates key/mouse at trigger time (~0.3–0.5 s) then returns to the background; optionally lock input before send to avoid conflicts.

---

## 📄 License

Released under the [MIT License](LICENSE).