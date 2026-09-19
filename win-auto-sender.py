# -*- coding: utf-8 -*-
"""
定时自动发送工具  -  现代版
向指定窗口定时粘贴并发送内容，支持单次/循环、多提示词轮转
"""
import sys
import os
import re
import time
import subprocess
import datetime
import random
import uuid
import ctypes
from ctypes import wintypes
import configparser
import pyperclip
import win32gui
import win32process
import win32con
import win32api
import win32event

# ── DPI 感知（必须在 QApplication 之前） ──────────────────────
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QComboBox, QPushButton, QSpinBox, QMessageBox,
    QListWidget, QListWidgetItem, QScrollArea, QPlainTextEdit,
    QDateTimeEdit, QCheckBox, QFrame, QSizePolicy,
    QTextEdit, QButtonGroup, QFileDialog, QLineEdit
)
from PySide6.QtCore import Qt, QTime, QDate, QDateTime, QThread, Signal, QTimer, QSize, QFileSystemWatcher
from PySide6.QtGui import QFont, QCursor, QPainter, QPen, QColor, QKeyEvent, QIcon, QPixmap, QBrush

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32


# ═══════════════════════════════════════════════════════════════
#  工具函数
# ═══════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════
#  配置文件管理（config-win-auto-sender.ini，与脚本同级目录）
# ═══════════════════════════════════════════════════════════════

PROMPT_SEP_RE = re.compile(r'^\s*-{3,}\s*$')   # --- 分割行
# 内部持久化分隔符：含不可见控制符，普通提示词几乎不可能出现，
# 避免内容含 === 或 --- 行时保存后重读被错误拆分（往返损坏）
PROMPT_SEP = '\n\x1F-WindowLoopSend-SEPARATOR-\x1F\n'


def _script_dir():
    """脚本或 exe 所在目录。exe 打包后 __file__ 指向临时解压目录，须改用 sys.executable。"""
    if getattr(sys, 'frozen', False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def default_config_file(instance=''):
    """配置文件路径。传入 instance（实例名）时派生独立配置文件，实现多开数据/配置隔离。"""
    name = "config-win-auto-sender.ini"
    if instance:
        name = "config-win-auto-sender-%s.ini" % instance
    return os.path.join(_script_dir(), name)


def split_prompt_text(text):
    """把多提示词文本分割成若干条（兼容各种换行）。
    - 若含内部不可见分隔符（PROMPT_SEP，程序写入），按它精确分割，无往返损坏
    - 否则按 '---' 行分割（用户导入 .txt/.md 友好），开头/结尾无分隔符也识别
    返回非空字符串列表。
    """
    if not text:
        return []
    if PROMPT_SEP in text:
        blocks = text.split(PROMPT_SEP)
    else:
        lines = text.split('\n')
        blocks = []
        cur = []
        for ln in lines:
            if PROMPT_SEP_RE.match(ln):
                blocks.append(cur)
                cur = []
            else:
                cur.append(ln)
        blocks.append(cur)
    # 合并：去掉纯空块，strip 每块首尾空白
    result = []
    for b in blocks:
        joined = ("\n".join(b) if isinstance(b, list) else b).strip()
        if joined:
            result.append(joined)
    return result


def compose_send_text(preface, prompt):
    """把「附加前言」合并到发送内容开头。前言为空则原样返回内容。"""
    if not preface:
        return prompt
    return preface.rstrip() + "\n" + prompt


def load_config_file(path=None):
    """从 ini 读取配置，返回 dict。文件不存在或无内容则返回默认。"""
    path = path or default_config_file()
    cfg = {
        'mode': 'single',
        'strategy': 'sequence',
        'interval_min': 10,
        'run_immediately': False,
        'click_x': 0,
        'click_y': 0,
        'window_title': '',
        'single_dt': None,          # 保留原始字符串，按需再解析
        'loop_start_dt': None,
        'loop_end_dt': None,
        'preface': '',
        'suffix': '',
        'suffix_delay': 0,
        'prompts': [],
    }
    if not os.path.exists(path):
        return cfg
    try:
        parser = configparser.ConfigParser(interpolation=None)
        parser.read(path, encoding='utf-8')
    except UnicodeDecodeError:
        # 外部工具可能以 GBK 保存 ini：UTF-8 解码失败时回退 GBK，避免静默丢失配置
        try:
            parser = configparser.ConfigParser(interpolation=None)
            parser.read(path, encoding='gbk')
        except Exception:
            return cfg
    except Exception:
        return cfg

    if parser.has_section('general'):
        g = parser['general']
        if g.get('mode', '').replace('"', '').strip() in ('single', 'loop'):
            cfg['mode'] = g.get('mode').strip().strip('"')
        if g.get('strategy', '').replace('"', '') in ('sequence', 'random'):
            cfg['strategy'] = g.get('strategy').strip().strip('"')
        for key in ('interval_min', 'click_x', 'click_y'):
            if key in g:
                try:
                    val = int(float(g[key].strip().strip('"')))
                    # 手改 ini 越界值安全夹紧：间隔 1~1440 分钟，坐标 0~32767
                    if key == 'interval_min':
                        val = max(1, min(1440, val))
                    else:
                        val = max(0, min(32767, val))
                    cfg[key] = val
                except Exception:
                    pass
        if 'run_immediately' in g:
            val = g['run_immediately'].strip().strip('"').lower()
            cfg['run_immediately'] = val in ('true', '1', 'yes', 'on')
        for key in ('window_title',):
            if key in g and g[key].strip():
                cfg[key] = g[key].strip().strip('"')

        if g.get('preface'):
            cfg['preface'] = g['preface']
        if g.get('suffix'):
            cfg['suffix'] = g['suffix']
        if 'suffix_delay' in g:
            try:
                val = int(float(g['suffix_delay'].strip().strip('"')))
                cfg['suffix_delay'] = max(0, min(86400, val))
            except Exception:
                pass

        # 日期时间字段：解析字符串 → datetime
        for key in ('single_dt', 'loop_start_dt', 'loop_end_dt'):
            if key in g and g[key].strip():
                try:
                    cfg[key] = datetime.datetime.strptime(
                        g[key].strip().strip('"'), '%Y-%m-%d %H:%M:%S')
                except Exception:
                    cfg[key] = g[key].strip().strip('"')

    # 发送内容（支持多段文本，用 --- 分隔）
    if parser.has_option('prompts', 'content'):
        raw = parser['prompts']['content']
        cfg['prompts'] = split_prompt_text(raw)
    return cfg


def save_config_file(cfg, path=None):
    """把配置写入 ini。路径使用 CONFIG_FILE（脚本同级）。"""
    path = path or default_config_file()
    try:
        parser = configparser.ConfigParser(interpolation=None)
        if os.path.exists(path):
            try:
                parser.read(path, encoding='utf-8')
            except Exception:
                parser = configparser.ConfigParser(interpolation=None)

        if not parser.has_section('general'):
            parser.add_section('general')
        gen = cfg['general'] if isinstance(cfg, dict) and 'general' in cfg else cfg
        parser['general']['mode'] = str(gen.get('mode', 'single'))
        parser['general']['strategy'] = str(gen.get('strategy', 'sequence'))
        parser['general']['interval_min'] = str(gen.get('interval_min', 10))
        parser['general']['run_immediately'] = str(bool(gen.get('run_immediately', False))).lower()
        parser['general']['click_x'] = str(gen.get('click_x', 0))
        parser['general']['click_y'] = str(gen.get('click_y', 0))
        parser['general']['window_title'] = str(gen.get('window_title', ''))
        parser['general']['preface'] = str(gen.get('preface', ''))
        parser['general']['suffix'] = str(gen.get('suffix', ''))
        parser['general']['suffix_delay'] = str(int(gen.get('suffix_delay', 0)))
        for dt_key in ('single_dt', 'loop_start_dt', 'loop_end_dt'):
            val = gen.get(dt_key, '')
            parser['general'][dt_key] = val.strftime('%Y-%m-%d %H:%M:%S') if hasattr(val, 'strftime') else str(val if val else '')

        # 发送内容：多个提示词用不可见分隔符（PROMPT_SEP）写入 [prompts] 的 content
        prompts = gen.get('prompts') if 'prompts' in gen else cfg.get('prompts', [])
        if not parser.has_section('prompts'):
            parser.add_section('prompts')
        parser.set('prompts', 'content', PROMPT_SEP.join(prompts))

        # 原子写入：先写临时文件再 os.replace，避免掉电/崩溃留下半写文件损坏配置
        tmp_path = path + '.tmp'
        # 仅清理本配置文件自身的历史残留同名 tmp（不动其它实例的 tmp，消除多实例并发误删）
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass
        with open(tmp_path, 'w', encoding='utf-8') as f:
            parser.write(f)
        os.replace(tmp_path, path)
    except Exception as e:
        # 不吞异常：让上层感知写盘失败（如 _persist 需据此保持 dirty 保护编辑内容）
        print(f"[配置写盘失败] {e}")
        raise


def is_admin():
    try:
        return ctypes.windll.shell32.IsUserAnAdmin()
    except Exception:
        return False


def force_foreground_window(hwnd):
    """突破限制激活窗口焦点"""
    if not hwnd or not win32gui.IsWindow(hwnd):
        return False
    if win32gui.IsIconic(hwnd):
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
    current_tid = kernel32.GetCurrentThreadId()
    target_tid, _ = win32process.GetWindowThreadProcessId(hwnd)
    attached = False
    ok_fore = False
    if current_tid != target_tid:
        attached = bool(user32.AttachThreadInput(current_tid, target_tid, True))
    try:
        win32api.keybd_event(win32con.VK_MENU, 0, 0, 0)
        win32api.keybd_event(win32con.VK_MENU, 0, win32con.KEYEVENTF_KEYUP, 0)
        ok_fore = bool(user32.SetForegroundWindow(hwnd))
        user32.BringWindowToTop(hwnd)
        user32.SetActiveWindow(hwnd)
    finally:
        if attached:
            user32.AttachThreadInput(current_tid, target_tid, False)
    time.sleep(0.15)
    # 仅以 SetForegroundWindow 返回值提示；不额外做 GetForegroundWindow 即时比对，
    # 因 Windows 前台切入为异步，即时比对会因窗口尚未落定而高频误报
    if not ok_fore:
        print("[提示] SetForegroundWindow 返回失败（可能受系统前台限制），发送将照常尝试；若落点异常请确认目标窗口权限。")
    return True


# 跨实例发送互斥锁：同一时刻只允许一个实例执行发送关键段，规避多开冲突
_SEND_MUTEX = win32event.CreateMutex(None, False, "Local\\WindowsLoopSend_SendMutex")


def click_and_paste_send(hwnd, text, click_pos=None):
    """点击 → 剪贴板粘贴 → 回车。
    发送动作前先全屏锁定鼠标/键盘输入（提前1秒），发送完成后自动解锁。
    BlockInput 模拟输入不受影响；需管理员权限，否则静默跳过锁定。

    多实例冲突规避：以进程级命名互斥锁保护整个发送关键段（复制剪贴板 +
    点击 + 粘贴 + 回车）。同一时刻只允许一个实例执行为止，其余实例排队，
    等待超时(30s)则返回失败、不发送，彻底规避并发剪贴板/焦点/输入竞争。
    """
    owned = False
    try:
        hr = win32event.WaitForSingleObject(_SEND_MUTEX, 30000)
        # WAIT_ABANDONED 也代表本线程已获得所有权（另一实例在关键段内被强杀），
        # 必须一并视为 owned，否则所有权泄漏导致后续发送持续失败
        owned = hr in (win32event.WAIT_OBJECT_0, win32event.WAIT_ABANDONED)
        if hr == win32event.WAIT_ABANDONED:
            print("[提示] 上次发送被强制终止，已接管发送互斥锁并继续。")
    except Exception as e:
        print(f"发送失败：获取发送互斥锁异常 {e}")
        return False
    if not owned:
        print("发送失败：等待发送互斥锁超时(>30s)，已跳过本条以规避并发输入竞争。")
        return False
    blocked = False
    try:
        # 提前1秒锁定真实鼠标键盘输入（未提权成功则 blocked 保持 False，正常发送）
        try:
            if user32.BlockInput(True):
                blocked = True
        except Exception:
            pass
        if blocked:
            time.sleep(1.0)

        if not force_foreground_window(hwnd):
            return False
        if click_pos and click_pos[0] > 0 and click_pos[1] > 0:
            x, y = click_pos
            user32.SetCursorPos(x, y)
            time.sleep(0.05)
            win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
            time.sleep(0.05)
            win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
            time.sleep(0.2)
        pyperclip.copy(text)
        time.sleep(0.05)
        win32api.keybd_event(win32con.VK_CONTROL, 0, 0, 0)
        win32api.keybd_event(ord('V'), 0, 0, 0)
        time.sleep(0.05)
        win32api.keybd_event(ord('V'), 0, win32con.KEYEVENTF_KEYUP, 0)
        win32api.keybd_event(win32con.VK_CONTROL, 0, win32con.KEYEVENTF_KEYUP, 0)
        time.sleep(0.15)
        win32api.keybd_event(win32con.VK_RETURN, 0, 0, 0)
        time.sleep(0.05)
        win32api.keybd_event(win32con.VK_RETURN, 0, win32con.KEYEVENTF_KEYUP, 0)
        return True
    except Exception as e:
        print(f"发送异常: {e}")
        return False
    finally:
        # 无论成功/失败/异常，发送结束都解锁鼠标键盘，避免锁死
        if blocked:
            try:
                user32.BlockInput(False)
            except Exception:
                pass
        if owned:
            try:
                win32event.ReleaseMutex(_SEND_MUTEX)
            except Exception:
                pass


def list_all_windows():
    """返回 [(title, hwnd), ...] 可见窗口列表"""
    windows = []
    def enum_cb(hwnd, extra):
        if win32gui.IsWindowVisible(hwnd):
            txt = win32gui.GetWindowText(hwnd)
            if txt.strip():
                windows.append((txt, hwnd))
        return True
    win32gui.EnumWindows(enum_cb, None)
    windows.sort(key=lambda x: x[0].lower())
    return windows


def compute_fire_list(mode, start_dt, end_dt, interval_min, immediate, max_count=12):
    """统一的「未来触发时间列表」计算函数——预览和 worker 共用同一套逻辑。
    返回 list[datetime]，可能为空。
    """
    now = datetime.datetime.now()
    if mode == 'single':
        return [start_dt] if start_dt > now else []

    interval = datetime.timedelta(minutes=interval_min)
    if start_dt >= end_dt:
        return []

    # 首次触发
    if immediate and now < end_dt:
        first = now
    else:
        first = start_dt
        if first <= now:
            elapsed = (now - first).total_seconds()
            steps = int(elapsed / interval.total_seconds()) + 1
            first = first + interval * steps
            if first > end_dt:
                return []

    if first > end_dt:
        return []

    result = []
    t = first
    while t <= end_dt and len(result) < max_count:
        result.append(t)
        t = t + interval
    return result


# ═══════════════════════════════════════════════════════════════
#  样式表
# ═══════════════════════════════════════════════════════════════

STYLE_SHEET = """
/* ═════════  极光蓝深色主题  ═════════ */

/* ===== 全局 ===== */
QWidget {
    font-family: "Microsoft YaHei UI", "Segoe UI", sans-serif;
    font-size: 10pt;
    color: #d6e4ff;
    background-color: #0b1220;
}
QMainWindow {
    background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #0a1628, stop:0.5 #0b1c36, stop:1 #091524);
}

/* 文字标签一律透明背景，避免继承 QWidget 默认底色形成黑块 */
QLabel {
    background: transparent;
}

/* ===== 卡片 ===== */
QFrame[class="card"] {
    background-color: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 #122038, stop:1 #0e1a2e);
    border: 1px solid #1f3560;
    border-radius: 14px;
}

QLabel[class="card-title"] {
    font-size: 12pt;
    font-weight: 600;
    color: #e0eeff;
    padding: 0px;
}

QLabel[class="card-icon"] {
    font-size: 14pt;
}

/* ===== 分段控件 ===== */
QPushButton[class="seg-btn"] {
    background-color: #0e1c33;
    color: #7a93c0;
    border: 1px solid #1f3560;
    padding: 8px 22px;
    font-weight: 500;
}
QPushButton[class="seg-btn-left"] {
    border-top-left-radius: 8px;
    border-bottom-left-radius: 8px;
    border-right: none;
}
QPushButton[class="seg-btn-right"] {
    border-top-right-radius: 8px;
    border-bottom-right-radius: 8px;
}
QPushButton[class="seg-btn"][active="true"] {
    background-color: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 #00b8ff, stop:1 #3d7bff);
    color: #ffffff;
    border-color: #00b8ff;
}

/* ===== 输入控件 ===== */
QComboBox, QSpinBox, QDateTimeEdit {
    background-color: #0e1c33;
    border: 1px solid #1f3560;
    border-radius: 6px;
    padding: 6px 10px;
    min-height: 20px;
    color: #d6e4ff;
    selection-background-color: #00b8ff;
    selection-color: #06101e;
}
QComboBox:hover, QSpinBox:hover, QDateTimeEdit:hover {
    border-color: #00b8ff;
}
QComboBox:focus, QSpinBox:focus, QDateTimeEdit:focus {
    border-color: #00d4ff;
    background-color: #122647;
}
QDateTimeEdit::drop-down {
    border: none;
    width: 24px;
    subcontrol-origin: padding;
    subcontrol-position: center right;
}
QComboBox::drop-down {
    border: none;
    width: 24px;
}
QComboBox QAbstractItemView {
    background: #122647;
    border: 1px solid #1f3560;
    border-radius: 6px;
    padding: 4px;
    color: #d6e4ff;
    selection-background-color: #1f3d7a;
    selection-color: #ffffff;
    outline: 0;
}

QCalendarWidget {
    background-color: #122647;
    color: #d6e4ff;
    selection-background-color: #00b8ff;
    selection-color: #06101e;
}
QCalendarWidget QToolButton {
    color: #d6e4ff;
    background: transparent;
    border: none;
}
QCalendarWidget QMenu {
    background: #122647;
    color: #d6e4ff;
}

QPlainTextEdit, QTextEdit {
    background-color: #0e1c33;
    border: 1px solid #1f3560;
    border-radius: 6px;
    padding: 6px;
    color: #d6e4ff;
    selection-background-color: #00b8ff;
    selection-color: #06101e;
}
QPlainTextEdit:focus, QTextEdit:focus {
    border-color: #00d4ff;
    background-color: #122647;
}

/* ===== 滚动条 ===== */
QScrollBar:vertical {
    background: transparent;
    width: 8px;
    margin: 4px 0;
}
QScrollBar::handle:vertical {
    background: #1f3560;
    border-radius: 4px;
    min-height: 30px;
}
QScrollBar::handle:vertical:hover {
    background: #3d5d94;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0;
}
QScrollBar:horizontal {
    background: transparent;
    height: 8px;
    margin: 0 4px;
}
QScrollBar::handle:horizontal {
    background: #1f3560;
    border-radius: 4px;
    min-width: 30px;
}
QScrollBar::handle:horizontal:hover {
    background: #3d5d94;
}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
    width: 0;
}

/* ===== 按钮 ===== */
QPushButton[class="btn-primary"] {
    background-color: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 #00b8ff, stop:1 #3d7bff);
    color: #ffffff;
    border: none;
    border-radius: 8px;
    padding: 10px 20px;
    font-weight: 600;
    font-size: 10.5pt;
}
QPushButton[class="btn-primary"]:hover {
    background-color: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 #00d4ff, stop:1 #5a92ff);
}
QPushButton[class="btn-primary"]:pressed {
    background-color: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 #00a0e0, stop:1 #2a6aff);
}
QPushButton[class="btn-primary"]:disabled {
    background-color: #1f3560;
    color: #5a7ab0;
}

QPushButton[class="btn-danger"] {
    background-color: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 #ff6b8a, stop:1 #e84868);
    color: #ffffff;
    border: none;
    border-radius: 8px;
    padding: 10px 20px;
    font-weight: 600;
    font-size: 10.5pt;
}
QPushButton[class="btn-danger"]:hover {
    background-color: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 #ff8aa3, stop:1 #ff5c7a);
}
QPushButton[class="btn-danger"]:pressed {
    background-color: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 #e85070, stop:1 #d43a58);
}

QPushButton[class="btn-secondary"] {
    background-color: #132547;
    color: #9cc0ff;
    border: 1px solid #1f3560;
    border-radius: 8px;
    padding: 8px 16px;
    font-weight: 500;
}
QPushButton[class="btn-secondary"]:hover {
    background-color: #1a3260;
    border-color: #3d5d94;
    color: #c2dbff;
}
QPushButton[class="btn-secondary"]:pressed {
    background-color: #0f1e3a;
}

QPushButton[class="btn-ghost"] {
    background-color: transparent;
    color: #00d4ff;
    border: 1px solid #00b8ff;
    border-radius: 6px;
    padding: 6px 14px;
    font-weight: 500;
}
QPushButton[class="btn-ghost"]:hover {
    background-color: rgba(0, 184, 255, 0.15);
}

QPushButton[class="btn-icon-del"] {
    background-color: #2a1a2e;
    color: #ff6b8a;
    border: 1px solid #4a2538;
    border-radius: 4px;
    font-weight: bold;
}
QPushButton[class="btn-icon-del"]:hover {
    background-color: #3d1f2b;
    border-color: #ff6b8a;
}

/* ===== 列表 ===== */
QListWidget {
    background-color: #0e1c33;
    border: 1px solid #1f3560;
    border-radius: 8px;
    padding: 4px;
    outline: 0;
    color: #d6e4ff;
}
QListWidget::item {
    padding: 8px 10px;
    border-radius: 4px;
    margin: 2px 0;
}
QListWidget::item:selected {
    background-color: #1f3d7a;
    color: #ffffff;
}

/* ===== 拖拽准星 ===== */
QFrame[class="picker-box"] {
    background-color: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 #0a1f3d, stop:1 #0e2a52);
    border: 2px dashed #00b8ff;
    border-radius: 10px;
}

/* ===== 复选框 ===== */
QCheckBox {
    spacing: 8px;
    color: #b8ccf0;
}
QCheckBox::indicator {
    width: 16px;
    height: 16px;
    border: 2px solid #3d5d94;
    border-radius: 4px;
    background: #0e1c33;
}
QCheckBox::indicator:hover {
    border-color: #00b8ff;
}
QCheckBox::indicator:checked {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 #00b8ff, stop:1 #3d7bff);
    border-color: #00d4ff;
}

/* ===== 状态文字（纯文字，无背景填充） ===== */
QLabel[class="status-pill"] {
    padding: 2px 4px;
    font-weight: 600;
    font-size: 10pt;
}
QLabel[class="status-pill"][state="idle"] {
    color: #7a93c0;
}
QLabel[class="status-pill"][state="running"] {
    color: #3dffa8;
}
QLabel[class="status-pill"][state="error"] {
    color: #ff8aa3;
}
QLabel[class="status-pill"][state="done"] {
    color: #5ad4ff;
}

/* ===== 倒计时 ===== */
QLabel[class="countdown"] {
    font-size: 18pt;
    font-weight: 700;
    color: #00d4ff;
    font-family: "Consolas", "Courier New", monospace;
}
QLabel[class="countdown-label"] {
    font-size: 9pt;
    color: #7a93c0;
}

/* ===== 日志 ===== */
QTextEdit[class="log-view"] {
    background-color: #06101e;
    color: #9cc0ff;
    border: 1px solid #1f3560;
    border-radius: 8px;
    font-family: "Consolas", "Courier New", monospace;
    font-size: 9pt;
}

/* ===== 标题文字 ===== */
QLabel#header-title {
    font-size: 15pt;
    font-weight: 700;
    color: #ffffff;
}
"""


# ═══════════════════════════════════════════════════════════════
#  拖拽准星控件
# ═══════════════════════════════════════════════════════════════

class TargetPickerLabel(QFrame):
    targetCaptured = Signal(int, int, int, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setProperty("class", "picker-box")
        self.setFixedSize(140, 90)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.dragging = False

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # 准星
        cx, cy = self.width() // 2, 34
        pen = QPen(QColor("#00d4ff"), 2)
        painter.setPen(pen)
        r = 14
        painter.drawEllipse(cx - r, cy - r, r * 2, r * 2)
        painter.drawLine(cx - r - 6, cy, cx + r + 6, cy)
        painter.drawLine(cx, cy - r - 6, cx, cy + r + 6)

        # 文字
        painter.setPen(QColor("#00d4ff"))
        font = painter.font()
        font.setPointSize(8)
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(self.rect().adjusted(0, 55, 0, -8), Qt.AlignmentFlag.AlignCenter, "按住拖向目标窗口")

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.dragging = True
            self.grabMouse(QCursor(Qt.CursorShape.CrossCursor))

    def mouseReleaseEvent(self, event):
        if self.dragging:
            self._finish_drag()
            pt = wintypes.POINT()
            user32.GetCursorPos(ctypes.byref(pt))
            x, y = pt.x, pt.y
            sub_hwnd = user32.WindowFromPoint(pt)
            hwnd = win32gui.GetAncestor(sub_hwnd, win32con.GA_ROOT)
            if not hwnd:
                hwnd = sub_hwnd
            title = win32gui.GetWindowText(hwnd) if hwnd else ""
            self.targetCaptured.emit(hwnd, x, y, title)

    def keyPressEvent(self, event: QKeyEvent):
        if event.key() == Qt.Key.Key_Escape and self.dragging:
            self._finish_drag()

    def focusOutEvent(self, event):
        if self.dragging:
            self._finish_drag()

    def _finish_drag(self):
        self.dragging = False
        self.releaseMouse()
        self.setCursor(Qt.CursorShape.PointingHandCursor)


# ═══════════════════════════════════════════════════════════════
#  调度线程
# ═══════════════════════════════════════════════════════════════

class TestSendThread(QThread):
    """「立即测试」发送在后台线程执行，避免占用 GUI 主线程导致界面冻结。"""
    done_signal = Signal(bool)

    def __init__(self, hwnd, text, click_pos, parent=None):
        super().__init__(parent)
        self.hwnd = hwnd
        self.text = text
        self.click_pos = click_pos

    def run(self):
        try:
            ok = click_and_paste_send(self.hwnd, self.text, self.click_pos)
        except Exception:
            ok = False
        self.done_signal.emit(ok)


class SchedulerWorker(QThread):
    log_signal = Signal(str)
    status_signal = Signal(str)       # idle / running / done
    next_fire_signal = Signal(object)  # datetime or None

    def __init__(self, config):
        super().__init__()
        self.config = config
        self.prompts = config['prompts']
        self.strategy = config['strategy']
        self.preface = config.get('preface', '')
        self.suffix = config.get('suffix', '')
        self.suffix_delay = int(config.get('suffix_delay', 0))
        self.prompt_index = 0

    def pick_prompt(self):
        if not self.prompts:
            return ''
        if self.strategy == 'random':
            return random.choice(self.prompts)
        p = self.prompts[self.prompt_index % len(self.prompts)]
        self.prompt_index = (self.prompt_index + 1) % len(self.prompts)
        return p

    def _next_fire(self, last_fire):
        mode = self.config['mode']
        if mode == 'single':
            return None
        interval = datetime.timedelta(minutes=self.config['interval_min'])
        nxt = last_fire + interval
        if nxt > self.config['end_dt']:
            return None
        return nxt

    def run(self):
        hwnd = self.config['hwnd']
        click_pos = self.config['click_pos']
        mode = self.config['mode']
        start_dt = self.config['start_dt']
        end_dt = self.config['end_dt']
        immediate = self.config.get('run_immediately', False)

        now = datetime.datetime.now()

        # 计算首次触发
        if mode == 'single':
            next_fire = start_dt
        else:
            if immediate and now < end_dt:
                next_fire = now
            else:
                next_fire = start_dt
                if next_fire <= now:
                    interval = datetime.timedelta(minutes=self.config['interval_min'])
                    elapsed = (now - next_fire).total_seconds()
                    steps = int(elapsed / interval.total_seconds()) + 1
                    next_fire = next_fire + interval * steps
                    if next_fire > end_dt:
                        next_fire = None

        if next_fire is None:
            self.log_signal.emit("已超出有效时间范围，无任务可执行。")
            self.status_signal.emit("done")
            return

        self.status_signal.emit("running")
        self.next_fire_signal.emit(next_fire)
        self.log_signal.emit(f"调度已启动，首次触发：{next_fire.strftime('%Y-%m-%d %H:%M:%S')}")

        while not self.isInterruptionRequested():
            now = datetime.datetime.now()
            if now >= next_fire:
                self.log_signal.emit("⏰ 触发 — 正在聚焦并发送...")
                ok = click_and_paste_send(hwnd, compose_send_text(self.preface, self.pick_prompt()), click_pos)
                self.log_signal.emit("✅ 发送成功。" if ok else "❌ 发送失败。")

                # 追加后续：延时指定时长后再发送
                if self.suffix.strip() and self.suffix_delay > 0:
                    self.log_signal.emit(f"🕒 {self.suffix_delay} 秒后发送追加后续...")
                    remaining = float(self.suffix_delay)
                    while remaining > 0 and not self.isInterruptionRequested():
                        time.sleep(min(0.2, remaining))
                        remaining -= 0.2
                    if not self.isInterruptionRequested():
                        ok2 = click_and_paste_send(hwnd, self.suffix, click_pos)
                        self.log_signal.emit("✅ 追加后续已发送。" if ok2 else "❌ 追加后续发送失败。")

                # 以实际完成时刻作为下一调度基线：若主发送+追加后续耗时较长，
                # 可避免 next_fire 仍落在过去导致背靠背二次发送
                now = datetime.datetime.now()
                next_fire = self._next_fire(now)
                if next_fire is None:
                    self.log_signal.emit("🏁 全部任务已完成，调度结束。")
                    break
                self.next_fire_signal.emit(next_fire)
                self.log_signal.emit(f"⏭️  下次触发：{next_fire.strftime('%Y-%m-%d %H:%M:%S')}")

            for _ in range(10):
                if self.isInterruptionRequested():
                    break
                time.sleep(0.1)

        self.status_signal.emit("done")
        self.next_fire_signal.emit(None)
        self.log_signal.emit("调度已停止。")


# ═══════════════════════════════════════════════════════════════
#  主窗口
# ═══════════════════════════════════════════════════════════════

class MainWindow(QMainWindow):
    def __init__(self, instance_name=''):
        super().__init__()
        self.instance_name = instance_name
        title = "定时自动发送工具  - yezijinn"
        if instance_name:
            title += f" · {instance_name}"
        if is_admin():
            title += "  ·  管理员模式"
        self.setWindowTitle(title)
        # 固定窗口：无拖动条，12:9 固定尺寸
        self.setFixedSize(1200, 900)

        self.worker = None
        self.prompt_cards = []
        self.next_fire_time = None  # 用于倒计时显示
        self._config_file = default_config_file(instance_name)
        self._loading = True     # 初始化期间抑制自动保存
        self._initializing = True  # 覆盖整个构造期（_set_mode 会再触发保存）
        self._pending_save = False
        self._dirty = False        # 标记是否有未落盘的本地改动，用于保护热更新不被覆盖
        self._user_touched = False # 标记用户是否真实改动过本实例（用于默认窗口关闭时静默删除）
        self._watcher = QFileSystemWatcher(self)
        if os.path.exists(self._config_file):
            self._watcher.addPath(self._config_file)
        self._watcher.fileChanged.connect(self._on_config_watcher)

        self._setup_style()
        self._init_ui()
        self._refresh_window_list()

        # 加载配置：若 ini 不存在则创建默认配置
        initial = load_config_file(self._config_file)
        if not os.path.exists(self._config_file):
            self._persist()
        self._apply_config(initial)

        self._on_mode_changed()
        self._refresh_schedule_preview()
        self._loading = False
        self._initializing = False
        self._pending_save = False
        if hasattr(self, '_debounce_timer'):
            self._debounce_timer.stop()

        # 每秒刷新：当前时间 + 倒计时 +（非运行时）预览
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._on_tick)
        self._timer.start(1000)

        if not is_admin():
            self._append_log("⚠️  未使用管理员权限运行，若目标窗口具有高权限可能无法键入。")

    # ── 样式 ────────────────────────────────────────────────

    def _setup_style(self):
        self.setStyleSheet(STYLE_SHEET)

    @staticmethod
    def _card(title_text, icon_text=""):
        """创建一张卡片，返回 (card_frame, content_layout, title_bar)"""
        card = QFrame()
        card.setProperty("class", "card")
        outer = QVBoxLayout(card)
        has_title = bool(title_text)
        # 无标题时减小上下留白，把空间留给卡片内容
        outer.setContentsMargins(16, 14 if has_title else 8, 16, 10)
        outer.setSpacing(12 if has_title else 4)

        # 标题栏（title_text 为空则不生成，卡片顶部直接是第一行正文）
        if has_title:
            title_bar = QHBoxLayout()
            title_bar.setSpacing(8)
            if icon_text:
                icon_lbl = QLabel(icon_text)
                icon_lbl.setProperty("class", "card-icon")
                title_bar.addWidget(icon_lbl)
            title_lbl = QLabel(title_text)
            title_lbl.setProperty("class", "card-title")
            title_bar.addWidget(title_lbl)
            title_bar.addStretch()
            outer.addLayout(title_bar)
            tb = title_bar
        else:
            tb = None

        content = QVBoxLayout()
        content.setSpacing(8)
        outer.addLayout(content)
        return card, content, tb

    # ── UI 构建 ─────────────────────────────────────────────

    def _init_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(10)

        # ─── 顶部状态栏 ───
        header = QHBoxLayout()
        header.setSpacing(10)
        title_lbl = QLabel("⏱️  定时发送")
        title_lbl.setStyleSheet("font-size: 15pt; font-weight: 700; color: #ffffff;")
        header.addWidget(title_lbl)
        header.addSpacing(12)

        btn_save_cfg = QPushButton("💾 保存配置")
        btn_save_cfg.setProperty("class", "btn-ghost")
        btn_save_cfg.setMinimumHeight(26)
        btn_save_cfg.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_save_cfg.setToolTip("立即把当前配置写入本地配置文件")
        btn_save_cfg.clicked.connect(self._save_config_now)
        header.addWidget(btn_save_cfg)

        btn_import_cfg = QPushButton("📥 导入配置")
        btn_import_cfg.setProperty("class", "btn-ghost")
        btn_import_cfg.setMinimumHeight(26)
        btn_import_cfg.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_import_cfg.setToolTip("从本地 ini 文件导入配置到当前窗口")
        btn_import_cfg.clicked.connect(self._import_config_file)
        header.addWidget(btn_import_cfg)

        btn_reset_cfg = QPushButton("♻️ 恢复出厂")
        btn_reset_cfg.setProperty("class", "btn-ghost")
        btn_reset_cfg.setMinimumHeight(26)
        btn_reset_cfg.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_reset_cfg.setToolTip("将当前窗口配置恢复为出厂默认（发送内容等全部清空）")
        btn_reset_cfg.clicked.connect(self._reset_factory_config)
        header.addWidget(btn_reset_cfg)

        header.addStretch()

        self.lbl_now = QLabel()
        self.lbl_now.setStyleSheet("color: #7a93c0; font-size: 9.5pt;")
        header.addWidget(self.lbl_now)

        self.status_pill = QLabel("● 无任务  空闲状态")
        self.status_pill.setProperty("class", "status-pill")
        self.status_pill.setProperty("state", "idle")
        self.status_pill.setStyle(self.style())  # 刷新属性
        header.addWidget(self.status_pill)

        root.addLayout(header)

        # ─── 主体：左右两列（固定窗口，无窗口级滚动条） ───
        body = QHBoxLayout()
        body.setContentsMargins(0, 2, 0, 4)
        body.setSpacing(12)

        # === 左列：目标窗口15% / 发送计划55% / 距下次触发15% / 运行日志15% ===
        left = QVBoxLayout()
        left.setContentsMargins(0, 0, 0, 0)
        left.setSpacing(10)

        target_card, target_layout, _ = self._card("目标窗口", "🎯")
        self._build_target_card(target_layout)
        left.addWidget(target_card, 3)

        sched_card, sched_layout, _ = self._card("")          # 发送计划（无大标题）
        self._build_schedule_card(sched_layout)
        left.addWidget(sched_card, 11)

        cd_card, cd_layout, _ = self._card("")                # 距下次触发（无大标题）
        self._build_countdown_card(cd_layout)
        left.addWidget(cd_card, 3)

        log_card, log_layout, _ = self._card("")              # 运行日志（无大标题）
        self._build_log_card(log_layout)
        left.addWidget(log_card, 3)

        body.addLayout(left, 11)

        # === 右列：发送内容占满整列高度 ===
        right = QVBoxLayout()
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(0)

        content_card, content_layout, _ = self._card("发送内容", "📝")
        self._build_content_card(content_layout)
        right.addWidget(content_card, 12)

        body.addLayout(right, 12)

        root.addLayout(body, 1)

        # ─── 底部操作栏（固定在窗口底部，不进滚动区） ───
        bar = QHBoxLayout()
        bar.setSpacing(10)

        self.btn_toggle = QPushButton("启动定时")
        self.btn_toggle.setProperty("class", "btn-primary")
        self.btn_toggle.setMinimumHeight(44)
        self.btn_toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_toggle.clicked.connect(self.toggle_task)
        bar.addWidget(self.btn_toggle, 2)

        btn_test = QPushButton("立即测试")
        btn_test.setProperty("class", "btn-secondary")
        btn_test.setMinimumHeight(44)
        btn_test.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_test.clicked.connect(self.test_trigger)
        bar.addWidget(btn_test, 1)

        btn_refresh = QPushButton("刷新预览")
        btn_refresh.setProperty("class", "btn-secondary")
        btn_refresh.setMinimumHeight(44)
        btn_refresh.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_refresh.clicked.connect(self._refresh_schedule_preview)
        bar.addWidget(btn_refresh, 1)

        btn_new = QPushButton("新建窗口 用于新目标")
        btn_new.setProperty("class", "btn-ghost")
        btn_new.setMinimumHeight(44)
        btn_new.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_new.clicked.connect(self._new_window)
        bar.addWidget(btn_new, 1)

        root.addLayout(bar)

    def _build_target_card(self, layout):
        # 拖拽 + 下拉
        row1 = QHBoxLayout()
        row1.setSpacing(10)

        self.picker_label = TargetPickerLabel()
        self.picker_label.targetCaptured.connect(self._on_target_captured)
        row1.addWidget(self.picker_label)

        right_col = QVBoxLayout()
        right_col.setSpacing(6)

        self.win_combo = QComboBox()
        self.win_combo.setMinimumWidth(200)
        self.win_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        btn_win_refresh = QPushButton("🔄 刷新")
        btn_win_refresh.setProperty("class", "btn-secondary")
        btn_win_refresh.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_win_refresh.clicked.connect(self._refresh_window_list)

        combo_row = QHBoxLayout()
        combo_row.addWidget(self.win_combo, 1)
        combo_row.addWidget(btn_win_refresh)
        right_col.addLayout(combo_row)
        self.win_combo.currentIndexChanged.connect(self._on_ui_changed_for_save)

        # 坐标
        coord_row = QHBoxLayout()
        self.spin_x = QSpinBox()
        self.spin_x.setRange(0, 9999)
        self.spin_x.setPrefix("X: ")
        self.spin_y = QSpinBox()
        self.spin_y.setRange(0, 9999)
        self.spin_y.setPrefix("Y: ")
        btn_clear_coord = QPushButton("清空坐标")
        btn_clear_coord.setProperty("class", "btn-secondary")
        btn_clear_coord.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_clear_coord.clicked.connect(lambda: (self.spin_x.setValue(0), self.spin_y.setValue(0)))
        self.spin_x.valueChanged.connect(self._on_ui_changed_for_save)
        self.spin_y.valueChanged.connect(self._on_ui_changed_for_save)
        coord_row.addWidget(self.spin_x)
        coord_row.addWidget(self.spin_y)
        coord_row.addWidget(btn_clear_coord)
        coord_row.addStretch()
        right_col.addLayout(coord_row)

        row1.addLayout(right_col, 1)
        layout.addLayout(row1)

    def _build_content_card(self, layout):
        # 附加前言（每次发送时自动置于内容开头）
        preface_head = QHBoxLayout()
        title_lbl = QLabel("[附加前言]")
        title_lbl.setStyleSheet("font-size: 9.5pt; font-weight: 700; color: #9fb7e6;")
        preface_head.addWidget(title_lbl)
        tip_lbl = QLabel("每一次发送时 自动将文本置于每一条发送内容的开头")
        tip_lbl.setStyleSheet("color: #5f739a; font-size: 8.5pt;")
        preface_head.addWidget(tip_lbl)
        preface_head.addStretch()
        layout.addLayout(preface_head)

        self.preface_edit = QPlainTextEdit()
        self.preface_edit.setPlaceholderText("（可选）在每条发送内容的开头 附加这一份相同内容...")
        self.preface_edit.setFixedHeight(int(self.preface_edit.fontMetrics().lineSpacing() * 3) + 12)
        self.preface_edit.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.preface_edit.textChanged.connect(self._on_ui_changed_for_save)
        layout.addWidget(self.preface_edit)

        # 追加后续（发送主内容后，延时指定时长再发送）
        suffix_head = QHBoxLayout()
        s_title = QLabel("[追加后续]")
        s_title.setStyleSheet("font-size: 9.5pt; font-weight: 700; color: #9fb7e6;")
        suffix_head.addWidget(s_title)
        s_tip = QLabel("发送每一条主内容之后 延时发送这里补充的内容")
        s_tip.setStyleSheet("color: #5f739a; font-size: 8.5pt;")
        suffix_head.addWidget(s_tip)
        suffix_head.addStretch()
        layout.addLayout(suffix_head)

        self.suffix_edit = QPlainTextEdit()
        self.suffix_edit.setPlaceholderText("（可选）每一次发送主内容之后 再延迟追加的后续内容...")
        self.suffix_edit.setFixedHeight(int(self.suffix_edit.fontMetrics().lineSpacing() * 3) + 12)
        self.suffix_edit.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.suffix_edit.textChanged.connect(self._on_ui_changed_for_save)
        layout.addWidget(self.suffix_edit)

        delay_row = QHBoxLayout()
        delay_row.setSpacing(8)
        delay_row.addWidget(QLabel("追加延时:"))
        self.suffix_delay_spin = QSpinBox()
        self.suffix_delay_spin.setRange(0, 86400)
        self.suffix_delay_spin.setValue(30)
        self.suffix_delay_spin.setSuffix(" 秒")
        self.suffix_delay_spin.setMinimumHeight(26)
        self.suffix_delay_spin.valueChanged.connect(self._on_ui_changed_for_save)
        delay_row.addWidget(self.suffix_delay_spin)
        delay_row.addWidget(QLabel("（循环模式，不得超过任务间隔，避免冲突）"))
        delay_row.addStretch()
        layout.addLayout(delay_row)

        # 滚动区
        self.prompt_scroll = QScrollArea()
        self.prompt_scroll.setWidgetResizable(True)
        self.prompt_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.prompt_container = QWidget()
        self.prompt_list_layout = QVBoxLayout(self.prompt_container)
        self.prompt_list_layout.setContentsMargins(0, 0, 0, 0)
        self.prompt_list_layout.setSpacing(6)
        self.prompt_list_layout.addStretch()
        self.prompt_scroll.setWidget(self.prompt_container)
        self.prompt_scroll.setMinimumHeight(180)
        layout.addWidget(self.prompt_scroll, 1)

        # 底部操作行
        bottom = QHBoxLayout()
        bottom.setSpacing(10)

        btn_add = QPushButton("➕ 添加一条内容")
        btn_add.setProperty("class", "btn-ghost")
        btn_add.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_add.clicked.connect(lambda: self.add_prompt_card())
        bottom.addWidget(btn_add)

        btn_import = QPushButton("📂 导入.txt内容(用---分割)")
        btn_import.setProperty("class", "btn-ghost")
        btn_import.setToolTip("导入 .txt / .md，用 --- 分隔多条发送内容")
        btn_import.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_import.clicked.connect(self._import_send_file)
        bottom.addWidget(btn_import)

        bottom.addSpacing(10)
        bottom.addWidget(QLabel("多条内容的发送方案:"))
        self.combo_strategy = QComboBox()
        self.combo_strategy.addItem("按顺序")
        self.combo_strategy.addItem("纯随机")
        self.combo_strategy.currentIndexChanged.connect(self._on_ui_changed_for_save)
        bottom.addWidget(self.combo_strategy)

        bottom.addStretch()
        self.lbl_prompt_count = QLabel("共 0 条")
        self.lbl_prompt_count.setStyleSheet("color: #7a93c0; font-size: 9pt;")
        bottom.addWidget(self.lbl_prompt_count)

        layout.addLayout(bottom)

    def _build_schedule_card(self, layout):
        # 分段控件：单次 / 循环
        seg_row = QHBoxLayout()
        seg_row.addStretch()

        self.btn_seg_single = QPushButton("单次发送")
        self.btn_seg_single.setProperty("class", "seg-btn seg-btn-left")
        self.btn_seg_single.setProperty("active", True)
        self.btn_seg_single.setCheckable(True)
        self.btn_seg_single.setChecked(True)
        self.btn_seg_single.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_seg_single.clicked.connect(lambda: self._set_mode("single"))

        self.btn_seg_loop = QPushButton("循环发送")
        self.btn_seg_loop.setProperty("class", "seg-btn seg-btn-right")
        self.btn_seg_loop.setProperty("active", False)
        self.btn_seg_loop.setCheckable(True)
        self.btn_seg_loop.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_seg_loop.clicked.connect(lambda: self._set_mode("loop"))

        seg_group = QButtonGroup(self)
        seg_group.addButton(self.btn_seg_single)
        seg_group.addButton(self.btn_seg_loop)
        seg_group.setExclusive(True)

        seg_row.addWidget(self.btn_seg_single)
        seg_row.addWidget(self.btn_seg_loop)
        seg_row.addStretch()
        layout.addLayout(seg_row)

        # —— 单次面板 ——
        self.single_panel = QWidget()
        sp = QVBoxLayout(self.single_panel)
        sp.setContentsMargins(2, 6, 2, 4)
        sp.setSpacing(10)

        label1 = QLabel("触发时间")
        label1.setStyleSheet("color: #7a93c0; font-size: 9pt; font-weight: 500;")
        sp.addWidget(label1)

        self.single_dt = QDateTimeEdit(QDateTime.currentDateTime().addSecs(60))
        self.single_dt.setDisplayFormat("yyyy-MM-dd  HH:mm:ss")
        self.single_dt.setCalendarPopup(True)
        self.single_dt.setMinimumHeight(30)
        sp.addWidget(self.single_dt)

        s_hint = QLabel("到点自动发送一次，然后停止")
        s_hint.setStyleSheet("color: #5a7ab0; font-size: 8.5pt;")
        s_hint.setWordWrap(True)
        sp.addWidget(s_hint)

        layout.addWidget(self.single_panel)

        # —— 循环面板 ——
        self.loop_panel = QWidget()
        lp = QVBoxLayout(self.loop_panel)
        lp.setContentsMargins(2, 6, 2, 4)
        lp.setSpacing(2)   # 三行紧凑，行间距接近 0，把高度让给下方预览列表

        # 开始
        self.loop_start_dt = QDateTimeEdit(QDateTime.currentDateTime().addSecs(60))
        self.loop_start_dt.setDisplayFormat("yyyy-MM-dd  HH:mm:ss")
        self.loop_start_dt.setCalendarPopup(True)
        self.loop_start_dt.setMinimumHeight(48)
        self.loop_start_dt.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        self.loop_start_dt.setStyleSheet("QDateTimeEdit { font-size: 12pt; font-weight: 600; }")
        lp.addWidget(self.loop_start_dt)

        # 结束
        self.loop_end_dt = QDateTimeEdit(QDateTime.currentDateTime().addDays(7))
        self.loop_end_dt.setDisplayFormat("yyyy-MM-dd  HH:mm:ss")
        self.loop_end_dt.setCalendarPopup(True)
        self.loop_end_dt.setMinimumHeight(48)
        self.loop_end_dt.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        self.loop_end_dt.setStyleSheet("QDateTimeEdit { font-size: 12pt; font-weight: 600; }")
        lp.addWidget(self.loop_end_dt)

        # 间隔 + 立即
        opt_row = QHBoxLayout()
        opt_row.setSpacing(8)
        opt_row.addWidget(QLabel("间隔:"))
        self.spin_interval = QSpinBox()
        self.spin_interval.setRange(1, 1440)
        self.spin_interval.setValue(10)
        self.spin_interval.setSuffix(" 分钟")
        self.spin_interval.setMinimumHeight(48)
        self.spin_interval.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        self.spin_interval.setStyleSheet("QSpinBox { font-size: 12pt; font-weight: 600; }")
        opt_row.addWidget(self.spin_interval)
        opt_row.addSpacing(12)
        self.chk_immediate = QCheckBox("启动时立即发第 1 次")
        opt_row.addWidget(self.chk_immediate)
        opt_row.addStretch()
        lp.addLayout(opt_row)

        layout.addWidget(self.loop_panel)

        # 分隔线
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setStyleSheet("color: #1f3560;")
        layout.addWidget(line)

        # 预览标题
        pv_title = QLabel("接下来的触发时间")
        pv_title.setStyleSheet("color: #7a93c0; font-size: 9pt; font-weight: 500;")
        layout.addWidget(pv_title)

        self.list_schedule = QListWidget()
        # 预览列表吸收循环设置三行紧凑后让出的垂直空间
        self.list_schedule.setMinimumHeight(90)
        self.list_schedule.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        layout.addWidget(self.list_schedule, 1)

        # 连接所有变化信号（预览刷新 + 自动保存）
        self.single_dt.dateTimeChanged.connect(self._refresh_schedule_preview)
        self.single_dt.dateTimeChanged.connect(self._on_ui_changed_for_save)
        self.loop_start_dt.dateTimeChanged.connect(self._refresh_schedule_preview)
        self.loop_start_dt.dateTimeChanged.connect(self._on_ui_changed_for_save)
        self.loop_end_dt.dateTimeChanged.connect(self._refresh_schedule_preview)
        self.loop_end_dt.dateTimeChanged.connect(self._on_ui_changed_for_save)
        self.spin_interval.valueChanged.connect(self._refresh_schedule_preview)
        self.spin_interval.valueChanged.connect(self._on_ui_changed_for_save)
        self.spin_interval.valueChanged.connect(lambda _: self._sync_suffix_limit())
        self.chk_immediate.toggled.connect(self._refresh_schedule_preview)
        self.chk_immediate.toggled.connect(self._on_ui_changed_for_save)

    def _build_countdown_card(self, layout):
        self.lbl_countdown = QLabel("--:--:--")
        self.lbl_countdown.setProperty("class", "countdown")
        self.lbl_countdown.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.lbl_countdown)

        self.lbl_countdown_label = QLabel("等待启动...")
        self.lbl_countdown_label.setProperty("class", "countdown-label")
        self.lbl_countdown_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.lbl_countdown_label)

    def _build_log_card(self, layout):
        self.log_view = QTextEdit()
        self.log_view.setProperty("class", "log-view")
        self.log_view.setReadOnly(True)
        self.log_view.setMinimumHeight(56)   # 日志卡片约占左列15%，保持紧凑
        self.log_view.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        layout.addWidget(self.log_view)

    # ── 模式切换 ─────────────────────────────────────────────

    def _set_mode(self, mode):
        is_single = (mode == "single")
        self.btn_seg_single.setProperty("active", is_single)
        self.btn_seg_loop.setProperty("active", not is_single)
        self.btn_seg_single.style().unpolish(self.btn_seg_single)
        self.btn_seg_single.style().polish(self.btn_seg_single)
        self.btn_seg_loop.style().unpolish(self.btn_seg_loop)
        self.btn_seg_loop.style().polish(self.btn_seg_loop)

        self.single_panel.setVisible(is_single)
        self.loop_panel.setVisible(not is_single)
        self._sync_suffix_limit()
        self._refresh_schedule_preview()
        self._on_ui_changed_for_save()

    def _sync_suffix_limit(self):
        """循环模式：追加后续延时不得超过任务间隔，避免与下一次触发冲突。"""
        spin = getattr(self, 'suffix_delay_spin', None)
        if spin is None:
            return
        is_single = self.btn_seg_single.property("active")
        if is_single:
            spin.setMaximum(86400)
        else:
            spin.setMaximum(max(1, self.spin_interval.value() * 60 - 1))
        # 若当前值超上限则自动收紧
        if spin.value() > spin.maximum():
            spin.setValue(spin.maximum())

    def _on_mode_changed(self):
        # 初始化时根据默认选中状态设置面板可见性
        self._set_mode("single")

    # ── 配置文件：持久化 + 热更新 ─────────────────────────────

    def _on_ui_changed_for_save(self, *_):
        """任何 UI 改动 → 立即持久化（含热更新的本地回写）。"""
        if self._is_loading():
            return
        # 用户在界面上真实改动过；程序回填（初始化/热更新）在 _is_loading 时已提前 return
        self._user_touched = True
        self._dirty = True
        self._pending_save = True
        if not hasattr(self, '_debounce_timer'):
            self._debounce_timer = QTimer(self)
            self._debounce_timer.setSingleShot(True)
            self._debounce_timer.timeout.connect(self._flush_pending_save)
        self._debounce_timer.start(400)

    def _set_loading_flag(self, on):
        self._loading = on

    def _is_loading(self):
        return bool(getattr(self, '_loading', False)) or bool(getattr(self, '_initializing', False))

    def _flush_pending_save(self):
        self._pending_save = False
        if self._is_loading():
            return
        self._persist()

    def _collect_persist_dict(self):
        """从当前 UI 收集配置状态（供写盘）。"""
        is_single = self.btn_seg_single.property("active")
        return {
            'mode': 'single' if is_single else 'loop',
            'strategy': 'random' if self.combo_strategy.currentIndex() == 1 else 'sequence',
            'interval_min': self.spin_interval.value(),
            'run_immediately': self.chk_immediate.isChecked(),
            'click_x': self.spin_x.value(),
            'click_y': self.spin_y.value(),
            'window_title': self.win_combo.currentText(),
            'single_dt': self._combine_dt(self.single_dt),
            'loop_start_dt': self._combine_dt(self.loop_start_dt),
            'loop_end_dt': self._combine_dt(self.loop_end_dt),
            'preface': self.preface_edit.toPlainText(),
            'suffix': self.suffix_edit.toPlainText(),
            'suffix_delay': self.suffix_delay_spin.value(),
            'prompts': self.get_prompt_list(),
        }

    def _persist(self):
        """把当前 UI 状态写入 ini。写盘前先临时摘除 watcher 防止热更新自触发。"""
        try:
            if self._watcher:
                self._watcher.removePath(self._config_file)
        except Exception:
            pass
        try:
            save_config_file({'general': self._collect_persist_dict()}, self._config_file)
            self._dirty = False
        except Exception as e:
            # 写盘失败：保持 dirty，避免误判已保存而失去对未落盘编辑的保护
            self._dirty = True
            print(f"[保存失败] 配置写入未成功：{e}")
        finally:
            try:
                if os.path.exists(self._config_file):
                    self._watcher.addPath(self._config_file)
            except Exception:
                pass

    def _apply_config(self, cfg):
        """把加载到的配置应用到 UI（热更新入口也用）。"""
        self._set_loading_flag(True)

        if cfg.get('mode') == 'loop':
            self._set_mode("loop")
        else:
            self._set_mode("single")

        if cfg.get('strategy') == 'random':
            self.combo_strategy.setCurrentIndex(1)
        else:
            self.combo_strategy.setCurrentIndex(0)

        self.spin_interval.setValue(int(cfg.get('interval_min', 10)))
        self.chk_immediate.setChecked(bool(cfg.get('run_immediately', False)))
        self.spin_x.setValue(int(cfg.get('click_x', 0)))
        self.spin_y.setValue(int(cfg.get('click_y', 0)))

        # 匹配目标窗口标题
        wt = cfg.get('window_title', '')
        if wt:
            idx = self.win_combo.findText(wt)
            if idx >= 0:
                self.win_combo.setCurrentIndex(idx)

        # 时间
        for widget, dt_val in (
            (self.single_dt, cfg.get('single_dt')),
            (self.loop_start_dt, cfg.get('loop_start_dt')),
            (self.loop_end_dt, cfg.get('loop_end_dt')),
        ):
            if dt_val and hasattr(dt_val, 'strftime'):
                widget.setDateTime(QDateTime(dt_val.year, dt_val.month, dt_val.day,
                                             dt_val.hour, dt_val.minute, dt_val.second))

        # 发送内容
        prompts = cfg.get('prompts', [])
        if not prompts:
            prompts = [""]
        self._set_prompts(prompts)
        self.preface_edit.setPlainText(cfg.get('preface', ''))
        self.suffix_edit.setPlainText(cfg.get('suffix', ''))
        self.suffix_delay_spin.setValue(int(cfg.get('suffix_delay', 0)))

        self._set_loading_flag(False)
        self._refresh_schedule_preview()

    def _on_config_watcher(self, path):
        """配置文件被外部修改 → 防抖后重新加载并同步 UI。"""
        if self._is_loading():
            return
        if self.worker and self.worker.isRunning():
            self._append_log("⚠️  检测到配置文件改动，任务运行中，待停再同步 UI。")
            return
        # 防抖
        if not hasattr(self, '_reload_timer'):
            self._reload_timer = QTimer(self)
            self._reload_timer.setSingleShot(True)
            self._reload_timer.timeout.connect(self._reload_from_disk)
        self._reload_timer.start(400)

    def _reload_from_disk(self):
        if getattr(self, '_dirty', False):
            self._append_log("⚠️  检测到本地未保存改动，已跳过文件热更新以保护正在编辑的内容。")
            return
        cfg = load_config_file(self._config_file)
        self._apply_config(cfg)
        self._append_log("🔄  配置热更新：已重新加载 config-win-auto-sender.ini")

    def _set_prompts(self, prompts):
        """用给定列表重建所有提示词卡片。"""
        for edit in list(self.prompt_cards):
            self._remove_prompt_card(edit)
        for text in prompts:
            if text:
                self.add_prompt_card(text)
        if not self.prompt_cards:
            self.add_prompt_card()

    # ── 导入发送内容文件 ──────────────────────────────────────

    def _import_send_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "导入发送内容", "",
            "文本 / Markdown (*.txt *.md);;所有文件 (*.*)")
        if not path:
            return
        try:
            with open(path, 'r', encoding='utf-8-sig') as f:
                text = f.read()
        except UnicodeDecodeError:
            try:
                with open(path, 'r', encoding='gbk') as f:
                    text = f.read()
            except Exception as e:
                QMessageBox.warning(self, "导入失败", f"无法读取文件：{e}")
                return
        prompts = split_prompt_text(text)
        if not prompts:
            QMessageBox.information(self, "导入", "文件中没有可识别的发送内容。")
            return
        self._set_prompts(prompts)
        self._append_log(f"📂  已导入 {len(prompts)} 条发送内容（来自 {os.path.basename(path)}）")
        self._persist()

    # ── 窗口列表 ─────────────────────────────────────────────

    def _refresh_window_list(self):
        self.win_combo.clear()
        for title, hwnd in list_all_windows():
            self.win_combo.addItem(f"{title}", hwnd)

    def _on_target_captured(self, hwnd, x, y, title):
        self.spin_x.setValue(x)
        self.spin_y.setValue(y)

        idx = -1
        for i in range(self.win_combo.count()):
            if self.win_combo.itemData(i) == hwnd:
                idx = i
                break
        if idx != -1:
            self.win_combo.setCurrentIndex(idx)
        else:
            self.win_combo.insertItem(0, title, hwnd)
            self.win_combo.setCurrentIndex(0)

        self._append_log(f"🎯 已捕获目标 — 窗口：{title}，坐标：({x}, {y})")

    # ── 提示词卡片 ──────────────────────────────────────────

    def add_prompt_card(self, text=''):
        idx = len(self.prompt_cards) + 1

        edit = QPlainTextEdit()
        # 每条固定占 3 行高度，不随内容动态变高
        line_h = edit.fontMetrics().lineSpacing()
        edit.setFixedHeight(int(line_h * 3) + 12)
        edit.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        edit.setPlaceholderText(f"第 {idx} 条内容（可多行，整块作为一条发送）")
        if text:
            edit.setPlainText(text)
        edit.textChanged.connect(self._update_prompt_count)
        edit.textChanged.connect(self._on_ui_changed_for_save)

        btn_del = QPushButton("✕")
        btn_del.setProperty("class", "btn-icon-del")
        btn_del.setFixedSize(24, 24)
        btn_del.setToolTip("删除该条")
        btn_del.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_del.clicked.connect(lambda: self._remove_prompt_card(edit))

        # 编号
        num_lbl = QLabel(f"<b style='color:#00d4ff;'>{idx}</b>")
        num_lbl.setFixedWidth(22)
        num_lbl.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter)
        num_lbl.setStyleSheet("padding-top: 6px;")

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        row.addWidget(num_lbl)
        row.addWidget(edit, 1)
        row.addWidget(btn_del, 0, Qt.AlignmentFlag.AlignTop)

        card = QFrame()
        card.setLayout(row)
        card.setStyleSheet("QFrame { background: #0e1c33; border: 1px solid #1f3560; border-radius: 6px; }")
        self.prompt_cards.append(edit)
        self.prompt_list_layout.insertWidget(self.prompt_list_layout.count() - 1, card)
        self.prompt_scroll.verticalScrollBar().setValue(self.prompt_scroll.verticalScrollBar().maximum())
        self._update_prompt_count()
        self._renumber_prompts()
        return edit

    def _remove_prompt_card(self, edit):
        if edit not in self.prompt_cards:
            return
        self.prompt_cards.remove(edit)
        card = edit.parentWidget()
        self.prompt_list_layout.removeWidget(card)
        card.deleteLater()
        self._update_prompt_count()
        self._renumber_prompts()

    def _renumber_prompts(self):
        """删除后重新编号"""
        for i, edit in enumerate(self.prompt_cards, 1):
            card = edit.parentWidget()
            num_lbl = card.findChild(QLabel)
            if num_lbl:
                num_lbl.setText(f"<b style='color:#00d4ff;'>{i}</b>")
            edit.setPlaceholderText(f"第 {i} 条内容（可多行，整块作为一条发送）")

    def get_prompt_list(self):
        return [edit.toPlainText().strip() for edit in self.prompt_cards if edit.toPlainText().strip()]

    def _update_prompt_count(self):
        n = len(self.get_prompt_list())
        self.lbl_prompt_count.setText(f"共 {n} 条" if n else "共 0 条")

    # ── 时间 / 预览 ──────────────────────────────────────────

    @staticmethod
    def _combine_dt(dt_edit):
        return dt_edit.dateTime().toPython()

    def _on_tick(self):
        """每秒执行一次：刷新当前时间 + 倒计时"""
        now = datetime.datetime.now()
        self.lbl_now.setText(now.strftime("%Y-%m-%d  %H:%M:%S"))

        # 倒计时
        nxt = self.next_fire_time
        if nxt is not None:
            delta = nxt - now
            total_secs = int(delta.total_seconds())
            if total_secs <= 0:
                self.lbl_countdown.setText("00:00:00")
                self.lbl_countdown_label.setText("即将触发...")
            else:
                h = total_secs // 3600
                m = (total_secs % 3600) // 60
                s = total_secs % 60
                self.lbl_countdown.setText(f"{h:02d}:{m:02d}:{s:02d}")
                self.lbl_countdown_label.setText(f"下次触发：{nxt.strftime('%Y-%m-%d %H:%M:%S')}")
        else:
            if self.worker and self.worker.isRunning():
                # 运行中但没有下次触发 = 正在执行最后一次
                pass
            else:
                self.lbl_countdown.setText("--:--:--")
                self.lbl_countdown_label.setText("待命中 · 配置好后点 启动定时")

        # 非运行时刷新预览：避免每秒全量重建。单次需秒级感知“已过去”翻转，
        # 循环列表不随秒变化，仅按分钟 + 配置值变化刷新（配置改动自身也会触发刷新）。
        if not (self.worker and self.worker.isRunning()):
            mode = "single" if self.btn_seg_single.property("active") else "loop"
            if mode == "single":
                fd = self._combine_dt(self.single_dt)
                key = "s:%s:%s" % (fd.strftime("%Y%m%d%H%M%S"), now.strftime("%Y%m%d%H%M%S"))
            else:
                key = "l:%s:%s:%s:%s:%s" % (
                    self._combine_dt(self.loop_start_dt).strftime("%Y%m%d%H%M%S"),
                    self._combine_dt(self.loop_end_dt).strftime("%Y%m%d%H%M%S"),
                    self.spin_interval.value(),
                    self.chk_immediate.isChecked(),
                    now.strftime("%Y%m%d%H%M"))
            if key != getattr(self, '_prev_preview_key', None):
                self._refresh_schedule_preview()
                self._prev_preview_key = key

    def _refresh_schedule_preview(self):
        self.list_schedule.clear()
        mode = "single" if self.btn_seg_single.property("active") else "loop"

        if mode == "single":
            fire_dt = self._combine_dt(self.single_dt)
            now = datetime.datetime.now()
            if fire_dt <= now:
                item = QListWidgetItem(f"⚠️  {fire_dt.strftime('%Y-%m-%d %H:%M:%S')}  （时间已过去，启动时会询问是否顺延）")
                item.setForeground(QColor("#ffaa3d"))
                self.list_schedule.addItem(item)
            else:
                item = QListWidgetItem(f"🕐  {fire_dt.strftime('%Y-%m-%d %H:%M:%S')}    单次 · 仅 1 次")
                item.setForeground(QColor("#00d4ff"))
                self.list_schedule.addItem(item)
            return

        # 循环
        start_dt = self._combine_dt(self.loop_start_dt)
        end_dt = self._combine_dt(self.loop_end_dt)
        interval_min = self.spin_interval.value()
        immediate = self.chk_immediate.isChecked()

        fires = compute_fire_list("loop", start_dt, end_dt, interval_min, immediate, max_count=12)

        if start_dt >= end_dt:
            item = QListWidgetItem("⚠️  开始时间必须早于结束时间")
            item.setForeground(QColor("#ff8aa3"))
            self.list_schedule.addItem(item)
            return

        if not fires:
            item = QListWidgetItem("⚠️  在结束时间之前已无触发机会")
            item.setForeground(QColor("#ff8aa3"))
            self.list_schedule.addItem(item)
            return

        for i, t in enumerate(fires):
            tag = "第 1 次" if i == 0 else f"第 {i+1} 次"
            item = QListWidgetItem(f"🕐  {t.strftime('%Y-%m-%d %H:%M:%S')}    {tag}")
            if i == 0:
                item.setForeground(QColor("#3dffa8"))
            self.list_schedule.addItem(item)

        # 判断是否还有更多
        last = fires[-1]
        nxt = last + datetime.timedelta(minutes=interval_min)
        if nxt <= end_dt:
            item = QListWidgetItem(f"  ……  还有更多次，直至 {end_dt.strftime('%Y-%m-%d %H:%M:%S')}")
            item.setForeground(QColor("#5a7ab0"))
            self.list_schedule.addItem(item)

    # ── 配置收集 ─────────────────────────────────────────────

    def _collect_config(self, for_test=False):
        hwnd = self.win_combo.currentData()
        if not hwnd or not win32gui.IsWindow(hwnd):
            QMessageBox.warning(self, "配置错误", "请先选择一个有效的目标窗口！")
            return None

        prompts = self.get_prompt_list()
        if not prompts:
            QMessageBox.warning(self, "配置错误", "请至少添加一条非空的发送内容！")
            return None

        is_single = self.btn_seg_single.property("active")
        now = datetime.datetime.now()

        if is_single:
            start_dt = self._combine_dt(self.single_dt)
            end_dt = start_dt
            if not for_test and start_dt <= now:
                reply = QMessageBox.question(
                    self, "时间已过去",
                    f"所设单次时间 {start_dt.strftime('%Y-%m-%d %H:%M:%S')} 已过去。\n是否自动顺延至明天同一时间？",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
                )
                if reply == QMessageBox.StandardButton.Yes:
                    start_dt = start_dt + datetime.timedelta(days=1)
                    end_dt = start_dt
                    self.single_dt.setDateTime(QDateTime(start_dt.year, start_dt.month, start_dt.day,
                                                         start_dt.hour, start_dt.minute, start_dt.second))
                else:
                    return None
        else:
            start_dt = self._combine_dt(self.loop_start_dt)
            end_dt = self._combine_dt(self.loop_end_dt)
            if start_dt >= end_dt:
                QMessageBox.warning(self, "配置错误", "开始时间必须早于结束时间！")
                return None
            # 追加后续延时不得超过任务间隔，避免与下一次触发冲突
            suffix_min = self.suffix_delay_spin.value() / 60.0
            if self.suffix_delay_spin.value() > 0 and suffix_min >= self.spin_interval.value():
                QMessageBox.warning(
                    self, "配置错误",
                    "追加后续的延时时长必须小于循环发送的间隔时长，否则会与下一次触发冲突。")
                return None

        return {
            'hwnd': hwnd,
            'prompts': prompts,
            'strategy': 'random' if self.combo_strategy.currentIndex() == 1 else 'sequence',
            'click_pos': (self.spin_x.value(), self.spin_y.value()),
            'mode': 'single' if is_single else 'loop',
            'start_dt': start_dt,
            'end_dt': end_dt,
            'interval_min': self.spin_interval.value(),
            'run_immediately': self.chk_immediate.isChecked(),
            'preface': self.preface_edit.toPlainText(),
            'suffix': self.suffix_edit.toPlainText(),
            'suffix_delay': self.suffix_delay_spin.value(),
        }

    # ── 任务控制 ─────────────────────────────────────────────

    def _new_window(self):
        """新开一个程序窗口，用于新的目标窗口：自动生成唯一实例名，独立进程/独立配置。"""
        name = "w" + uuid.uuid4().hex[:8]
        if getattr(sys, 'frozen', False):
            args = [sys.executable, "--name", name]
        else:
            args = [sys.executable, os.path.abspath(__file__), "--name", name]
        try:
            subprocess.Popen(args, creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
        except Exception as e:
            QMessageBox.warning(self, "无法新开窗口", "启动新实例失败：%s" % e)
            return
        self._append_log(f"已新开窗口（实例 {name}），用于新的目标窗口。")

    def toggle_task(self):
        if self.worker and self.worker.isRunning():
            # 停止
            self.worker.requestInterruption()
            while not self.worker.wait(100):
                QApplication.processEvents()
            self.worker = None
            self.next_fire_time = None
            self.btn_toggle.setText("▶  启动定时")
            self.btn_toggle.setProperty("class", "btn-primary")
            self.btn_toggle.style().unpolish(self.btn_toggle)
            self.btn_toggle.style().polish(self.btn_toggle)
            self._set_status("idle", "● 已停止")
            self._append_log("⏹️  任务已停止。")
            return

        cfg = self._collect_config()
        if not cfg:
            return

        self.worker = SchedulerWorker(cfg)
        self.worker.log_signal.connect(self._append_log)
        self.worker.status_signal.connect(self._on_worker_status)
        self.worker.next_fire_signal.connect(self._on_next_fire)
        self.worker.start()

        self.btn_toggle.setText("■  停止定时")
        self.btn_toggle.setProperty("class", "btn-danger")
        self.btn_toggle.style().unpolish(self.btn_toggle)
        self.btn_toggle.style().polish(self.btn_toggle)
        self._set_status("running", "● 运行中")

    def _on_worker_status(self, status):
        if status == "running":
            self._set_status("running", "● 运行中")
        elif status == "done":
            self._set_status("done", "● 已完成")
            self.btn_toggle.setText("▶  启动定时")
            self.btn_toggle.setProperty("class", "btn-primary")
            self.btn_toggle.style().unpolish(self.btn_toggle)
            self.btn_toggle.style().polish(self.btn_toggle)
            self.next_fire_time = None
            self.worker = None

    def _on_next_fire(self, nxt):
        self.next_fire_time = nxt

    def _set_status(self, state, text):
        self.status_pill.setText(text)
        self.status_pill.setProperty("state", state)
        self.status_pill.style().unpolish(self.status_pill)
        self.status_pill.style().polish(self.status_pill)

    # ── 测试 ────────────────────────────────────────────────

    def test_trigger(self):
        cfg = self._collect_config(for_test=True)
        if not cfg:
            return
        # 互斥守卫：上一次测试仍在发送时拒绝再开，杜绝覆盖引用导致线程孤儿崩溃
        if getattr(self, '_test_busy', False):
            self._append_log("⚠️  已有一次测试发送进行中，请稍候再试。")
            return
        self._append_log("⚡  执行测试发送...")
        prompts = cfg['prompts']
        prompt = random.choice(prompts) if cfg['strategy'] == 'random' else prompts[0]
        text = compose_send_text(cfg.get('preface', ''), prompt)
        # 后台线程发送，防止阻塞 GUI（发送含提前 1s 锁输入 + 键鼠模拟，内部互斥排队最坏数秒）
        self._test_busy = True
        self._test_thread = TestSendThread(cfg['hwnd'], text, cfg['click_pos'])
        self._test_thread.done_signal.connect(self._on_test_done)
        try:
            self._test_thread.start()
        except Exception as e:
            # 启动失败时复位 busy，避免永久锁死后续测试
            self._test_busy = False
            self._test_thread = None
            self._append_log(f"❌  启动测试发送失败：{e}")

    def _on_test_done(self, ok):
        self._test_busy = False
        if ok:
            self._append_log("✅  测试完成：已聚焦并发送。")
        else:
            self._append_log("❌  测试失败：请确认目标窗口权限是否高于本程序。")
        self._test_thread = None

    # ── 日志 ────────────────────────────────────────────────

    def _append_log(self, msg):
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        self.log_view.append(f"[{ts}]  {msg}")
        # 自动滚到底
        sb = self.log_view.verticalScrollBar()
        sb.setValue(sb.maximum())

    # ── 关闭 ────────────────────────────────────────────────

    def closeEvent(self, event):
        if self.worker and self.worker.isRunning():
            self.worker.requestInterruption()
            while not self.worker.wait(100):
                QApplication.processEvents()
        tt = getattr(self, '_test_thread', None)
        if tt and tt.isRunning():
            # 测试发送内含最长约 30s 的发送互斥等待，轮询等待其结束，避免孤儿线程残留
            waited = 0
            while tt.isRunning() and waited < 35000:
                tt.wait(200)
                waited += 200
                QApplication.processEvents()
        if not self._cleanup_tmp_config():
            event.ignore()
            return
        event.accept()

    def _cleanup_tmp_config(self):
        """附属窗口（--name）关闭时的配置回收策略，返回是否允许关闭。
        未配置任何内容 → 判定为垃圾直接删除，不打扰；
        已配置内容 → 弹窗询问保留或删除，由用户决定，避免误删；
        默认窗口绝不处理。
        """
        if not self.instance_name:
            return True
        # 先把当前内存 UI 状态真正落盘，避免 400ms 防抖尚未写盘的编辑被误判为空而删除
        try:
            self._flush_pending_save()
        except Exception:
            pass
        # 用户从未在界面上真实改动过（默认新建未使用）→ 视为垃圾，静默删除配置直接关闭，不打扰
        if not self._user_touched:
            try:
                if os.path.exists(self._config_file):
                    os.remove(self._config_file)
                    print(f"[清理] 已删除未使用的实例配置: {self._config_file}")
            except Exception:
                pass
            return True
        try:
            cfg = load_config_file(self._config_file)
        except Exception:
            cfg = {}
        has_data = bool(any(cfg.get('prompts') or [])) or bool(cfg.get('window_title')) \
            or bool(cfg.get('preface')) or bool(cfg.get('suffix'))
        if not has_data:
            try:
                if os.path.exists(self._config_file):
                    os.remove(self._config_file)
                    print(f"[清理] 已删除未使用的实例配置: {self._config_file}")
            except Exception:
                pass
            return True
        box = QMessageBox(self)
        box.setWindowTitle("关闭实例")
        box.setIcon(QMessageBox.Icon.Question)
        box.setText(f"实例「{self.instance_name}」已配置了发送内容。\n关闭后该实例配置文件何处理？")
        box.setInformativeText(self._config_file)
        keep_btn = box.addButton("保留配置", QMessageBox.ButtonRole.AcceptRole)
        del_btn = box.addButton("删除配置", QMessageBox.ButtonRole.DestructiveRole)
        box.addButton("取消关闭", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        clicked = box.clickedButton()
        if clicked is del_btn:
            try:
                if os.path.exists(self._config_file):
                    os.remove(self._config_file)
                    print(f"[清理] 已删除实例配置: {self._config_file}")
            except Exception:
                pass
            return True
        if clicked is keep_btn:
            print(f"[保留] 实例「{self.instance_name}」配置保留于: {self._config_file}")
            return True
        print("[取消] 已取消关闭窗口。")
        return False

    # ── 配置管理：保存 / 导入 / 恢复出厂 ─────────────────────────────

    def _save_config_now(self):
        """手动立即保存当前配置到本地配置文件（替代仅靠防抖后台保存）。"""
        self._persist()
        self._append_log(f"💾 已保存配置：{self._config_file}")

    def _import_config_file(self):
        """从用户选择的 ini 文件导入配置到当前窗口，并覆盖当前窗口的配置文件。"""
        if self.worker and self.worker.isRunning():
            QMessageBox.warning(self, "不可导入", "任务运行中，请先停止再导入配置。")
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "选择要导入的配置文件", _script_dir(),
            "配置文件 (*.ini *.INI);;所有文件 (*.*)")
        if not path:
            return
        try:
            cfg = load_config_file(path)
        except Exception as e:
            QMessageBox.warning(self, "导入失败", "无法解析配置文件：%s" % e)
            return
        self._apply_config(cfg)
        self._persist()
        self._append_log(f"✅ 已从 {path} 导入配置到当前窗口。")

    def _reset_factory_config(self):
        """把当前窗口配置恢复为出厂默认（清空发送内容、重置全部参数）。"""
        if self.worker and self.worker.isRunning():
            QMessageBox.warning(self, "不可重置", "任务运行中，请先停止再恢复出厂。")
            return
        reply = QMessageBox.question(
            self, "恢复出厂",
            "将清空当前窗口的全部发送内容与设置，恢复出厂默认。\n确定继续吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply != QMessageBox.StandardButton.Yes:
            return
        now = datetime.datetime.now()
        default_cfg = {
            'mode': 'single',
            'strategy': 'sequence',
            'interval_min': 10,
            'run_immediately': False,
            'click_x': 0,
            'click_y': 0,
            'window_title': '',
            'single_dt': now + datetime.timedelta(seconds=60),
            'loop_start_dt': now + datetime.timedelta(seconds=60),
            'loop_end_dt': now + datetime.timedelta(days=7),
            'preface': '',
            'suffix': '',
            'suffix_delay': 0,
            'prompts': [''],
        }
        self._apply_config(default_cfg)
        self._persist()
        self._append_log("♻️  已恢复出厂默认配置。")


# ═══════════════════════════════════════════════════════════════
#  入口
# ═══════════════════════════════════════════════════════════════

if __name__ == '__main__':
    # 支持实例名 --name/-n：每个实例使用独立配置文件，实现数据/配置完全隔离
    instance_name = ''
    args = sys.argv[1:]
    for i, a in enumerate(args):
        if a in ('--name', '-n') and i + 1 < len(args):
            instance_name = args[i + 1].strip()
    # 实例名做白名单校验，防路径穿越（../ 或含 / \ .. 等绕过 exe 同级目录）
    if instance_name and not re.fullmatch(r'[A-Za-z0-9_\-]+', instance_name):
        print(f"[忽略] 非法实例名「{instance_name}」，已回退为默认实例。")
        instance_name = ''

    app = QApplication(sys.argv)
    app.setStyle('Fusion')
    font = QFont("Microsoft YaHei UI", 9)
    app.setFont(font)
    win = MainWindow(instance_name)
    win.show()
    sys.exit(app.exec())
