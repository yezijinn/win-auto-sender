<p align="center">
  <img src="docs/main-page.png" alt="WindowsLoopSend 主界面" width="760"/>
</p>

# 🚀 WindowsLoopSend · 定时指令自动发送工具

> 轻量、无侵入的 Windows 自动化工具 —— 定时把提示词自动粘贴并回车发送到指定窗口，全程零插件、零侵入。

<p align="center">
  <a href="README-EN.md"><strong>🌍 English</strong></a> ·
  <b>中文</b>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/平台-Windows_10%2F11-0078d4" alt="Platform"/>
  <img src="https://img.shields.io/badge/界面-PySide6-blue" alt="GUI"/>
  <img src="https://img.shields.io/badge/打包-单文件_EXE-brightgreen" alt="Build"/>
  <img src="https://img.shields.io/badge/协议-MIT-yellow" alt="License"/>
</p>

面对 OpenCode、VS Code、Windows Terminal、CMD、浏览器等目标，本工具通过底层 API 抢占窗口焦点，配合剪贴板注入与键鼠事件模拟，实现**定时自动发送**，也能开多个窗口同时管理多个目标。

---

## ✨ 核心特性

| 能力 | 说明 |
|---|---|
| 🎯 **准星拖拽瞄准** | Spy++ 风格：按住准星拖到目标输入框松开，自动捕获窗口句柄与物理坐标 |
| 🧲 **突破抢焦点拦截** | `AttachThreadInput` + 键盘状态模拟，穿透 Windows 防后台偷焦限制 |
| 🖥️ **高分屏 DPI 自适应** | 原生 `SetProcessDpiAwareness`，2K/4K 与 125%/150% 缩放坐标不偏移 |
| 📋 **剪贴板原子输入** | `Ctrl+V` 粘贴，告别长文本丢字、标点乱码与输入法拦截 |
| 🔁 **多提示词轮转** | 多条独立卡片，支持**顺序循环**与**随机发送**两种策略 |
| 📝 **[附加前言]** | 可选文本，合并到每条发送内容的开头 |
| ⏱️ **[追加后续]** | 主内容触发后延时 N 秒再发一段，循环模式自动限制不超间隔 |
| ⏰ **单次 / 循环调度** | 单次指定时间发一次即停；循环设间隔分钟、可立即首发、可限定起止日期 |
| 🪟 **多实例隔离** | 一键新开独立窗口管理另一个目标，各自配置/内容/定时互不影响 |
| 💾 **配置管理** | 自动保存(400ms 防抖) + 热更新 + 手动保存/导入/恢复出厂 |
| 🔒 **发送前锁键鼠** | 可选项：发送前约 1 秒锁定鼠标键盘，避免与手动操作冲突 |

---

## 🚀 快速上手

### 1. 克隆并安装依赖

```bash
git clone https://github.com/yezijinn/win-auto-sender.git
cd win-auto-sender
pip install PySide6 pywin32 pyperclip
```

### 2. 运行

> **⚠️ 重要（UAC 权限）**：若目标窗口以**管理员身份**运行（如管理员版 Terminal / VS Code / CMD），本工具**必须同样以管理员运行**，否则 Windows UIPI 会静默拦截键鼠消息。

```bash
python win-auto-sender.py
```

或直接双击 `dist\WindowsLoopSend.exe`（单文件，目标机无需装 Python）。

---

## 🖱️ 使用步骤

1. **绑定窗口**：左键按住右上角 **🎯 准星**，拖到目标软件输入区松开，自动绑定窗口并回填坐标；也可从下拉框直接选窗口。
2. **填提示词**：在「发送内容」填多条内容（每条卡片可多行）；可用 **[附加前言]** / **[追加后续]**；选择「顺序循环」或「随机」。
3. **设触发**：单次填时间；循环填间隔分钟、可勾选立即首发、可设起止日期。
4. **测试再挂机**：先点 **⚡ 立即测试触发 1 次** 确认能弹窗回车，再点 **▶ 启动定时监听** 挂机。

---

## 📥 导入发送内容

支持一键导入 `发送内容.txt` / `发送内容.md`，程序会**按 `---` 自动分割**成多条独立提示词（开头/末尾缺 `---` 也能智能识别）；也可通过顶栏 **📥 导入配置** 载入 `.ini` 配置。

<p align="center">
  <img src="docs/导入文本列表.png" alt="导入文本列表" width="640"/>
</p>

---

## 🪟 多窗口 / 多目标

点击 **「🆕 新建窗口（用于新的目标）」** 即可新开一个完全独立的程序实例——**独立进程 + 独立配置文件**（`config-<实例名>-win-auto-sender.ini`），发送内容、前言/后续、定时、目标窗口全部独立，互不干扰。

<p align="center">
  <img src="docs/双开程序.png" alt="多窗口双开示例" width="760"/>
</p>

> 启动时也可用 `--name <实例名>` 指定专属配置；**未作任何修改的新建窗口关闭时自动回收清理配置**，不留垃圾文件。

---

## 💾 配置管理

- 配置文件：`config-win-auto-sender.ini`（与 exe 同级）。
- UI 修改自动保存（**400ms 防抖**）；外部改文件会**热更新**回 UI。
- 顶栏提供 **💾 保存配置 / 📥 导入配置 / ♻️ 恢复出厂**；导入与恢复在任务运行中会被拦截。

<p align="center">
  <img src="docs/关闭程序.png" alt="关闭窗口配置回收提示" width="560"/>
</p>

---

## 🔧 构建打包

PyInstaller 单文件打包，目标机无需任何运行库：

```bash
python build.py
# 产物：dist\WindowsLoopSend.exe
```

---

## 📁 项目结构

```text
win-auto-sender/
├── win-auto-sender.py    # 入口：GUI、抢焦点、剪贴板注入、后台调度
├── build.py              # 一键 PyInstaller 打包
├── WindowsLoopSend.spec  # PyInstaller 配置
├── docs/                 # 界面截图
├── README.md / README-EN.md
└── .gitignore
```

## 📦 依赖

```text
PySide6>=6.5.0
pywin32>=306
pyperclip>=1.8.2
```

---

## 💬 FAQ

**Q：日志显示“已发送”但目标窗口没内容？**
A：目标进程权限更高（如管理员终端）。退出后用**右键 → 以管理员身份运行**重启。

**Q：测试时窗口弹出但光标不在输入框？**
A：用准星重新拖到输入框中心松开，确保 `X/Y` 坐标非 0。

**Q：触发会干扰我当前操作吗？**
A：触发瞬间会拉前台取焦并模拟键鼠（约 0.3~0.5 秒），随后恢复后台；可开启发送前锁键鼠避免冲突。

---

## 📄 开源协议

本项目采用 [MIT License](LICENSE) 开源。