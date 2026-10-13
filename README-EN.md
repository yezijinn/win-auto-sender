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
  <img src="https://img.shields.io/badge/License-GPL--3.0-blue" alt="License"/>
</p>

Works against targets such as OpenCode, VS Code, Windows Terminal, CMD and browsers: it grabs window focus via low-level APIs, injects text through the clipboard plus simulated key/mouse events, and fires the prompt automatically on schedule. Open multiple windows to drive several targets at once.

---

## ✨ Core Features

| Capability | Description |
|---|---|
| 🎯 **Crosshair Drag-To-Aim** | Spy++-style: drag the crosshair onto the target input box and release to bind the window and append that point as a click step |
| 🖱️ **Click Sequence** | Multiple click points per target, executed in order: custom coordinates (negative coordinates for secondary monitors supported), reorderable (drag / ↑↓) and a **per-step delay in ms** (new steps default to 1000 ms) |
| ⌨️ **Key Sequence** | Keys sent after pasting: `Enter` / `Tab` / `Esc` / `F1`–`F12` / `Ctrl+A` and other single or combined keys, each with its **own delay** (new steps default to 1000 ms); the **⌨** button on a step opens a floating full-size keyboard for click-to-enter (numpad and nav keys included; light up Ctrl/Shift/Alt/Win first to record a combo) |
| 📋 **Paste Switch** | Turn the `Ctrl+V` paste off to send only keystrokes (e.g. shortcut-only actions) |
| 🧲 **Bypass Focus-Stealing Protection** | `AttachThreadInput` + keyboard-state simulation to overcome background-focus restrictions |
| 🖥️ **High-DPI Aware** | Native `SetProcessDpiAwareness` — coordinates stay correct at 2K/4K and 125%/150% scaling |
| 📋 **Clipboard Atomic Input** | `Ctrl+V` pasting — no dropped chars, corrupted punctuation, or IME interference |
| 🗂️ **Master-Detail Content Workspace** | Prompt list on the left, editor on the right: add / duplicate / delete / drag-to-reorder — **list order is send order** — plus one-click `.txt/.md` import |
| 🔁 **Multiple Prompt Rotation** | **Sequential loop** or **pure random** sending strategies |
| 📝 **[Preface]** | Optional text (collapsible section) prepended to the start of every sent message |
| ⏱️ **[Suffix]** | Optional text (collapsible section) sent N seconds after the main message; in loop mode the delay is capped below the interval |
| ⏰ **Single / Loop Scheduling** | Single fires once then stops; loop sets a minute interval, optional immediate first fire, date-range protection and a live preview of upcoming fire times |
| 🧩 **Three-Column Settings** | Target + click sequence / schedule / send behaviour side by side, all visible at once; content and the run log below at full width; window size and column widths are remembered |
| 🗂️ **Collapsible Sections** | Click sequence, key sequence, schedule preview, preface and suffix are all collapsible sections showing step/item counts in the header — long lists cost no screen space |
| 🪟 **Mini Window** | The toolbar's “🪟 Mini” opens a minimal window with only three things: change time, edit text, stop/resume; dock it against a screen edge to auto-collapse into a thin strip that expands on hover; mutually exclusive with the main window — only one is shown at a time |
| ✏️ **Custom Program Name** | New windows are named “BaseName-N” (`WindowsLoopSend-2`, `-3` …) and can be renamed to any Chinese/English name; the config file follows the new name |
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
┌─ Toolbar: status · clock · countdown · save/import/restore/new/rename/mini ──────────────┐
│ ┌─ 🎯 Target + click sequence ─┐ ┌─ ⏰ Schedule ─────────┐ ┌─ ⚙️ Send behaviour ───┐ │
│ │ crosshair · dropdown · refresh│ │ single / loop         │ │ paste switch · lock   │ │
│ │ ▾ click points: order + delay │ │ start / end / interval│ │ ▾ key sequence: keys  │ │
│ │ ▾ per-step delay (ms)         │ │ immediate + ▾ preview │ │ ▾ per-step delays    │ │
│ └───────────────────────────────┘ └───────────────────────┘ └───────────────────────┘ │
│ ┌─ 📝 Content (full width) ─────────────────────────────────────────────────────────┐ │
│ │ toolbar: add/dup/del/move/import + strategy ｜ list ｜ editor ｜ ▾ preface/suffix   │ │
│ └──────────────────────────────────────────────────────────────────────────────────┘ │
│ ▾ Run log (collapsible)                                                               │
│ Action bar: [▶ Start] [⚡ Test Send]                                      save-state    │
└──────────────────────────────────────────────────────────────────────────────────────┘
```

The settings area holds three side-by-side columns (draggable widths, each column scrolls internally); content and the run log sit below at full width. Click sequence, key sequence, schedule preview, preface and suffix are all **▾ collapsible sections** (collapsed headers show step/item counts). The window is **freely resizable** (min 960×620) and remembers its size, column widths, section heights and collapse states.

## 🖱️ Usage

1. **Bind window and click points**: press and hold the **🎯 crosshair** in the settings area, drag to the target input area and release — the window is bound and that point is **appended** as a click step. Repeat to click several places (input box → send button), then reorder with **↑/↓** and set a **delay (ms)** per step. With no click point at all the tool only foregrounds the window.
2. **Enter prompts**: click **➕ Add** in the content area, then write in the editor (multi-line supported); reorder with **⬆/⬇** or by dragging the list items; optionally fill **[Preface]** / **[Suffix]**.
3. **Set trigger**: single fires at a chosen time; loop sets a minute interval, optional immediate first fire and start/end dates, with a live preview of the next fire times.
4. **Configure send behaviour**: decide whether to **paste the content (Ctrl+V)** and edit the **key sequence** — one `Enter` by default, replaceable with `Tab`, `F5`, `Ctrl+A` and other single or combined keys, each with its own delay; if unsure of a key name, click the **⌨** button on the step and click keys on the floating full-size keyboard — for combos, light up Ctrl/Shift/Alt/Win first, then click the target key.
5. **Test, then run**: click **⚡ Test Send** first to confirm focus, clicks, paste and keys all land correctly, then **▶ Start** to keep it running.

---

## 🪟 Mini Window

Click **“🪟 Mini”** in the toolbar to open a minimal window that keeps only three things — **change time / edit text / stop-resume** — so it can stay parked in a screen corner.

- **Change time**: edit the fire time directly (the single-task time, or the loop start time), or click “Now” to fill in the current moment.
- **Edit text**: switch items with the dropdown and edit the text in place; changes are written back to the main window and saved immediately.
- **Stop / resume**: the status line shows the live state, countdown and next fire time, with a one-click **⏹ Stop / ▶ Resume**.
- **QQ-style edge docking**: drag it against the left/right screen edge to snap; close to the edge it auto-collapses into a thin strip that expands on mouse hover and collapses again when you leave. Top/bottom edges snap without collapsing. Position and docking state are remembered.
- **Exclusive with the main window**: opening the mini window hides the main window, and closing the mini window via its **↗** or **✕** button restores the main window — only one is shown at a time.

---

## 📥 Import Prompts

Click **📂 Import** in the content workspace to load `发送内容.txt` / `发送内容.md` in one click — the app **auto-splits by `---`** into independent prompts (missing markers at the start/end are handled intelligently) which you can then edit and reorder in the list. You can also load a `.ini` config via the **📥 Import Config** button.

<p align="center">
  <img src="docs/导入文本列表.png" alt="Import prompt list" width="640"/>
</p>

---

## 🪟 Multi-Window / Multi-Target

Click **“🆕 New Window”** in the toolbar to open a fully independent instance — **separate process + separate config file** (`config-win-auto-sender-<name>.ini`). Prompts, preface/suffix, schedule and target are all independent per instance.

New windows are named **“BaseName-N”**: `WindowsLoopSend-2`, `WindowsLoopSend-3` … (the smallest free number is picked, so naming never collides). Click **“✏️ Rename”** in the toolbar to give it any Chinese/English name (≤48 chars, limited to letters, digits, `_`, `-` and Chinese characters) — the **config file is migrated to the new name** and the window title updates instantly. Renaming the default window is remembered, so the next launch without arguments reuses that name.

<p align="center">
  <img src="docs/双开程序.png" alt="Multi-window example" width="760"/>
</p>

> On startup you may use `--name <name>` to select a dedicated config. A brand-new window that was **never modified is cleaned up silently on close** — no leftover files, no annoying prompts.

---

## 💾 Config Management

- Config file: `config-win-auto-sender.ini` for the default window, `config-win-auto-sender-<name>.ini` for other instances (all next to the exe); renaming an instance migrates its config file automatically.
- UI changes auto-save with a **400 ms debounce**; editing the file externally **hot-syncs** back to the UI; the bottom bar shows a live “unsaved / saved at” state.
- Toolbar provides **💾 Save / 📥 Import / ♻️ Restore-to-factory**; import & restore are blocked while a task is running.
- Window size, section heights, log visibility, section collapse states and mini-window position are remembered separately — they never pollute the ini config.

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
├── LICENSE               # Full GPL-3.0 text
├── BUG.md                # Known defects and suggested fixes
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

**Q: Why is “Start” blocked on a brand-new window before a target is picked?**
A: The target dropdown stays on the “请选择目标窗口” placeholder by default; without a real window bound, Start is refused so content can never be sent to an unintended program.

**Q: The log says “config parse failed, read-only protection”?**
A: That ini has a syntax or encoding problem. The app opens with defaults and **will not write back automatically**, so your original file is preserved; fix the file and restart, or click “💾 保存” to explicitly overwrite with the current content.

**Q: Two windows share the same instance name — what happens?**
A: They share one config file. When the later window closes it **will not reclaim that config**, so the config in use by the other instance is preserved.

**Q: Do two instances collide when firing at the same time?**
A: The send critical section is serialized by a process-level mutex, so only one instance clicks and types at a time. If the queue wait exceeds 30 seconds the firing is skipped this round and the reason is written to the run log.

---

## 📄 License

Released under [GPL-3.0](LICENSE): strong copyleft, derivative works must also be GPL.