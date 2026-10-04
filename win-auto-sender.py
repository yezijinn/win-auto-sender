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
import json
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
    QDateTimeEdit, QCheckBox, QFrame, QSizePolicy, QSplitter,
    QTextEdit, QButtonGroup, QFileDialog, QAbstractItemView, QLineEdit, QAbstractSpinBox
)
from PySide6.QtCore import (
    Qt, QDateTime, QThread, Signal, QTimer, QFileSystemWatcher, QSettings
)
from PySide6.QtGui import QFont, QCursor, QPainter, QPen, QColor, QKeyEvent

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32


# ═══════════════════════════════════════════════════════════════
#  工具函数
# ═══════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════
#  配置文件管理（config-win-auto-sender.ini，与脚本同级目录）
# ═══════════════════════════════════════════════════════════════

PROMPT_SEP_RE = re.compile(r'^\s*-{3,}\s*$')   # --- 分割行
# 内部持久化分隔符（内存内使用）
PROMPT_SEP = '\n\x1F-WindowLoopSend-SEPARATOR-\x1F\n'
# 历史遗留：该分隔符写入 ini 后，configparser 读取多行值会剥离行首尾空白，
# 而 Python 把 \x1F 也视作空白字符，导致 \x1F 被剥离、分隔符失效（多条内容被合并）。
# 故此正则用于识别「已退化」的历史分隔行，把旧配置重新正确拆分。
PROMPT_SEP_LEGACY_RE = re.compile(r'^[\s\x00-\x1f]*-WindowLoopSend-SEPARATOR-[\s\x00-\x1f]*$')

# 单次触发时间的「已过去」容忍窗口（秒）。秒级精度下，把触发时间设为当前时刻
# （如点「获取此时」）不应立刻被判为过期，否则每次都会弹「是否顺延」。
PAST_TOLERANCE_SEC = 2


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
    - 若含内部不可见分隔符（PROMPT_SEP，程序写入），按它精确分割
    - 否则按分隔行分割（'---' 行，或历史遗留的 SEPARATOR 行；用户导入 .txt/.md 友好）
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
            if PROMPT_SEP_RE.match(ln) or PROMPT_SEP_LEGACY_RE.match(ln):
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


def _decode_prompts(parser):
    """解析 [prompts] 内容。新格式为 JSON 数组（format=json，单行、无空白剥离风险）；
    旧格式回退按分隔行拆分。"""
    if not parser.has_option('prompts', 'content'):
        return []
    raw = parser['prompts']['content']
    fmt = parser['prompts'].get('format', '').strip() if parser.has_option('prompts', 'format') else ''
    if fmt == 'json':
        try:
            data = json.loads(raw)
            if isinstance(data, list):
                return [str(x).strip() for x in data if str(x).strip()]
        except Exception:
            pass
    return split_prompt_text(raw)


def load_config_file(path=None):
    """从 ini 读取配置，返回 dict。文件不存在或无内容则返回默认。"""
    path = path or default_config_file()
    cfg = {
        'mode': 'single',
        'strategy': 'sequence',
        'interval_min': 10,
        'run_immediately': False,
        'lock_input': True,
        'click_x': 0,
        'click_y': 0,
        'window_title': '',
        'single_dt': None,          # 保留原始字符串，按需再解析
        'loop_start_dt': None,
        'loop_end_dt': None,
        'preface': '',
        'suffix': '',
        'suffix_delay': 0,
        'lock_input': True,
        'paste': True,
        'click_steps': [],          # [{'x': int, 'y': int, 'delay_ms': int}, ...]
        'key_steps': [],            # [{'key': 'enter', 'delay_ms': int}, ...]
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
        if 'lock_input' in g:
            val = g['lock_input'].strip().strip('"').lower()
            cfg['lock_input'] = val in ('true', '1', 'yes', 'on')
        if 'paste' in g:
            val = g['paste'].strip().strip('"').lower()
            cfg['paste'] = val in ('true', '1', 'yes', 'on')
        # 点击序列 / 按键序列：单行 JSON 数组，逐项做类型收敛，坏项直接丢弃
        for key in ('click_steps', 'key_steps'):
            if key not in g or not g[key].strip():
                continue
            try:
                data = json.loads(g[key].strip().strip('"'))
            except Exception:
                continue
            if not isinstance(data, list):
                continue
            steps = []
            for item in data:
                if not isinstance(item, dict):
                    continue
                try:
                    delay = max(0, min(3600000, int(float(item.get('delay_ms', 0)))))
                except Exception:
                    delay = 0
                if key == 'click_steps':
                    try:
                        steps.append({'x': max(-32767, min(32767, int(float(item.get('x', 0))))),
                                      'y': max(-32767, min(32767, int(float(item.get('y', 0))))),
                                      'delay_ms': delay})
                    except Exception:
                        continue
                else:
                    name = str(item.get('key', '')).strip()
                    if name:
                        steps.append({'key': name, 'delay_ms': delay})
            cfg[key] = steps
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

    # 发送内容（多条发送内容）
    cfg['prompts'] = _decode_prompts(parser)
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
        parser['general']['lock_input'] = str(bool(gen.get('lock_input', True))).lower()
        parser['general']['paste'] = str(bool(gen.get('paste', True))).lower()
        parser['general']['click_steps'] = json.dumps(gen.get('click_steps') or [], ensure_ascii=False)
        parser['general']['key_steps'] = json.dumps(gen.get('key_steps') or [], ensure_ascii=False)
        parser['general']['click_x'] = str(gen.get('click_x', 0))
        parser['general']['click_y'] = str(gen.get('click_y', 0))
        parser['general']['window_title'] = str(gen.get('window_title', ''))
        parser['general']['preface'] = str(gen.get('preface', ''))
        parser['general']['suffix'] = str(gen.get('suffix', ''))
        parser['general']['suffix_delay'] = str(int(gen.get('suffix_delay', 0)))
        for dt_key in ('single_dt', 'loop_start_dt', 'loop_end_dt'):
            val = gen.get(dt_key, '')
            parser['general'][dt_key] = val.strftime('%Y-%m-%d %H:%M:%S') if hasattr(val, 'strftime') else str(val if val else '')

        # 发送内容：以 JSON 数组写入 [prompts]（单行存储，避免 configparser
        # 对多行值剥离行首尾空白而破坏分隔符）
        prompts = gen.get('prompts') if 'prompts' in gen else cfg.get('prompts', [])
        if not parser.has_section('prompts'):
            parser.add_section('prompts')
        parser.set('prompts', 'format', 'json')
        parser.set('prompts', 'content', json.dumps([str(p) for p in prompts], ensure_ascii=False))

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
        # Windows 上目标文件可能被其它进程（编辑器/杀软/同名实例）瞬时占用导致
        # os.replace 抛 PermissionError，做有限次短重试而非直接判定写盘失败
        last_err = None
        for attempt in range(3):
            try:
                os.replace(tmp_path, path)
                last_err = None
                break
            except PermissionError as e:
                last_err = e
                time.sleep(0.06 * (attempt + 1))
        if last_err is not None:
            raise last_err
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


# 按键名 → 虚拟键码。除英文名外兼容少量中文写法（回车 / 空格 / 上 等）
VK_NAME_MAP = {
    'enter': 0x0D, 'return': 0x0D, '回车': 0x0D,
    'tab': 0x09, '制表': 0x09,
    'esc': 0x1B, 'escape': 0x1B,
    'space': 0x20, 'spacebar': 0x20, '空格': 0x20,
    'backspace': 0x08, '退格': 0x08,
    'delete': 0x2E, 'del': 0x2E, '删除': 0x2E,
    'insert': 0x2D, 'ins': 0x2D,
    'home': 0x24, 'end': 0x23,
    'pageup': 0x21, 'pgup': 0x21, 'pagedown': 0x22, 'pgdn': 0x22,
    'up': 0x26, 'down': 0x28, 'left': 0x25, 'right': 0x27,
    '上': 0x26, '下': 0x28, '左': 0x25, '右': 0x27,
    'ctrl': 0x11, 'control': 0x11, 'shift': 0x10, 'alt': 0x12,
    'win': 0x5B, 'lwin': 0x5B, 'rwin': 0x5C, 'apps': 0x5D,
    'capslock': 0x14, 'numlock': 0x90, 'scrolllock': 0x91,
    'printscreen': 0x2C, 'pause': 0x13,
    'minus': 0xBD, '-': 0xBD, 'equal': 0xBB, '=': 0xBB,
    'comma': 0xBC, ',': 0xBC, 'period': 0xBE, '.': 0xBE,
    'slash': 0xBF, '/': 0xBF, 'backslash': 0xDC, '\\': 0xDC,
    'semicolon': 0xBA, ';': 0xBA, 'quote': 0xDE, "'": 0xDE,
    'bracketleft': 0xDB, '[': 0xDB, 'bracketright': 0xDD, ']': 0xDD,
    'grave': 0xC0, '`': 0xC0,
}
for _n in range(1, 25):
    VK_NAME_MAP['f%d' % _n] = 0x6F + _n        # VK_F1 = 0x70


def parse_key_combo(text):
    """把 'enter' / 'Ctrl+A' / 'F5' 解析为虚拟键码列表；无法识别返回 None。"""
    parts = [p.strip() for p in str(text).replace('＋', '+').split('+') if p.strip()]
    if not parts:
        return None
    vks = []
    for p in parts:
        low = p.lower()
        if low in VK_NAME_MAP:
            vks.append(VK_NAME_MAP[low])
        elif len(p) == 1 and p.isascii() and p.isalnum():
            vks.append(ord(p.upper()))
        else:
            return None
    return vks


def press_key_combo(text):
    """按下并释放一个按键组合（多个键用 + 连接表示同时按下）。"""
    vks = parse_key_combo(text)
    if not vks:
        return False
    try:
        for vk in vks:
            win32api.keybd_event(vk, 0, 0, 0)
            time.sleep(0.02)
        for vk in reversed(vks):
            win32api.keybd_event(vk, 0, win32con.KEYEVENTF_KEYUP, 0)
            time.sleep(0.02)
        return True
    except Exception as e:
        print(f"按键失败 {text!r}：{e}")
        return False


def wait_interruptible(seconds, should_stop=None):
    """可被打断的等待，返回 False 表示等待期间收到停止请求。"""
    remaining = float(seconds)
    while remaining > 0:
        if should_stop is not None and should_stop():
            return False
        time.sleep(min(0.05, remaining))
        remaining -= 0.05
    return True


def run_click_steps(steps, should_stop=None):
    """按顺序点击各坐标点，每一步先等待自身的间隔；返回 (已完成步数, 是否被打断)。"""
    done = 0
    for step in steps or []:
        # 间隔为 0 时也要先看一次停止请求，避免停不下来时仍多执行一步
        if should_stop is not None and should_stop():
            return done, True
        if not wait_interruptible(float(step.get('delay_ms', 0)) / 1000.0, should_stop):
            return done, True
        try:
            user32.SetCursorPos(int(step.get('x', 0)), int(step.get('y', 0)))
            time.sleep(0.03)
            win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
            time.sleep(0.03)
            win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
        except Exception as e:
            print(f"点击失败 {step!r}：{e}")
            return done, False
        done += 1
    return done, False


def run_key_steps(steps, should_stop=None):
    """按顺序发送按键，每一步先等待自身的间隔；返回 (已完成步数, 失败按键, 是否被打断)。"""
    done = 0
    for step in steps or []:
        if should_stop is not None and should_stop():
            return done, None, True
        if not wait_interruptible(float(step.get('delay_ms', 0)) / 1000.0, should_stop):
            return done, None, True
        name = str(step.get('key', '')).strip()
        if not name:
            continue
        if not press_key_combo(name):
            return done, name, False
        done += 1
    return done, None, False


def click_and_paste_send(hwnd, text, click_steps=None, key_steps=None,
                         paste=True, lock_input=True, should_stop=None):
    """聚焦目标窗口 → 按序列点击 → 粘贴内容 → 按序列发送按键。

    click_steps / key_steps 为步骤列表，每一步自带「执行前等待」的毫秒间隔；
    传空列表表示跳过该阶段。lock_input 为真时发送前锁定真实鼠标键盘约 1 秒，
    BlockInput 模拟输入不受影响；需管理员权限，否则静默跳过锁定。

    多实例冲突规避：以进程级命名互斥锁保护整个发送关键段（点击 + 剪贴板 +
    粘贴 + 按键）。同一时刻只允许一个实例执行，其余实例排队，
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
        # 启用时提前1秒锁定真实鼠标键盘输入（未提权成功则 blocked 保持 False，正常发送）
        try:
            if lock_input and user32.BlockInput(True):
                blocked = True
        except Exception:
            pass
        if blocked:
            time.sleep(1.0)

        if not force_foreground_window(hwnd):
            return False

        # 点击序列：按配置顺序逐个点击，每步按自身间隔等待
        steps = list(click_steps or [])
        if steps:
            done, interrupted = run_click_steps(steps, should_stop)
            if interrupted:
                print("发送中止：收到停止请求。")
                return False
            if done < len(steps):
                print("发送失败：点击序列未执行完。")
                return False
            time.sleep(0.2)        # 点击落定后再粘贴

        if paste:
            pyperclip.copy(text)
            time.sleep(0.05)
            win32api.keybd_event(win32con.VK_CONTROL, 0, 0, 0)
            win32api.keybd_event(ord('V'), 0, 0, 0)
            time.sleep(0.05)
            win32api.keybd_event(ord('V'), 0, win32con.KEYEVENTF_KEYUP, 0)
            win32api.keybd_event(win32con.VK_CONTROL, 0, win32con.KEYEVENTF_KEYUP, 0)
            time.sleep(0.15)

        # 按键序列：默认一条回车，可换成任意按键组合
        key_list = list(key_steps or [])
        if key_list:
            done, failed, interrupted = run_key_steps(key_list, should_stop)
            if interrupted:
                print("发送中止：收到停止请求。")
                return False
            if failed is not None:
                print(f"发送失败：无法识别的按键 {failed!r}。")
                return False
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
/* 注意：class 属性为 "seg-btn seg-btn-left" 这类多词值，须用 ~= 词匹配，= 精确匹配不会命中 */
QPushButton[class~="seg-btn"] {
    background-color: #0e1c33;
    color: #7a93c0;
    border: 1px solid #1f3560;
    padding: 8px 22px;
    font-weight: 500;
}
QPushButton[class~="seg-btn-left"] {
    border-top-left-radius: 8px;
    border-bottom-left-radius: 8px;
    border-right: none;
}
QPushButton[class~="seg-btn-right"] {
    border-top-right-radius: 8px;
    border-bottom-right-radius: 8px;
}
QPushButton[class~="seg-btn"][active="true"] {
    background-color: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 #00b8ff, stop:1 #3d7bff);
    color: #ffffff;
    border-color: #00b8ff;
}

/* 「获取此时」：与分段控件等高，独立圆角 */
QPushButton[class="btn-now"] {
    background-color: #0e1c33;
    color: #00d4ff;
    border: 1px solid #1f3560;
    border-radius: 8px;
    padding: 8px 18px;
    font-weight: 500;
}
QPushButton[class="btn-now"]:hover {
    background-color: rgba(0, 184, 255, 0.15);
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

/* ===== 日志 ===== */
QTextEdit[class="log-view"] {
    background-color: #06101e;
    color: #9cc0ff;
    border: 1px solid #1f3560;
    border-radius: 8px;
    font-family: "Consolas", "Courier New", monospace;
    font-size: 9pt;
}

/* ===== 工具栏 / 操作栏 ===== */
QFrame[class="toolbar"], QFrame[class="actionbar"] {
    background-color: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 #101f38, stop:1 #0d1a30);
    border: 1px solid #1f3560;
    border-radius: 10px;
}

QLabel[class="app-title"] {
    font-size: 13pt;
    font-weight: 700;
    color: #ffffff;
}

QLabel[class="meta"] {
    color: #7a93c0;
    font-size: 9.5pt;
}

QLabel[class="hint"] {
    color: #5f739a;
    font-size: 8.5pt;
}

/* 倒计时胶囊 */
QLabel[class="chip"] {
    background-color: #0e1c33;
    border: 1px solid #1f3560;
    border-radius: 11px;
    padding: 2px 12px;
    color: #00d4ff;
    font-family: "Consolas", "Courier New", monospace;
    font-size: 11pt;
    font-weight: 700;
}

/* 迷你按钮（工具栏 / 内容工具条） */
QPushButton[class="btn-mini"] {
    background-color: #132547;
    color: #9cc0ff;
    border: 1px solid #1f3560;
    border-radius: 6px;
    padding: 4px 10px;
    font-weight: 500;
}
QPushButton[class="btn-mini"]:hover {
    background-color: #1a3260;
    border-color: #3d5d94;
    color: #c2dbff;
}
QPushButton[class="btn-mini"]:pressed {
    background-color: #0f1e3a;
}

/* ===== 步骤序列（点击序列 / 按键序列） ===== */
QLabel[class="step-title"] {
    color: #9fb7e6;
    font-size: 9.5pt;
    font-weight: 700;
}
QFrame[class="step-row"] {
    background-color: #0e1c33;
    border: 1px solid #1f3560;
    border-radius: 6px;
}
QPushButton[class="btn-step"] {
    background-color: #132547;
    color: #9cc0ff;
    border: 1px solid #1f3560;
    border-radius: 4px;
    font-weight: 600;
    padding: 0;
}
QPushButton[class="btn-step"]:hover {
    background-color: #1a3260;
    border-color: #3d5d94;
}
QPushButton[class="btn-step-del"] {
    background-color: #2a1a2e;
    color: #ff8aa3;
    border: 1px solid #4a2538;
    border-radius: 4px;
    font-weight: 600;
    padding: 0;
}
QPushButton[class="btn-step-del"]:hover {
    background-color: #3d1f2b;
    border-color: #ff6b8a;
}
QLineEdit {
    background-color: #122647;
    border: 1px solid #1f3560;
    border-radius: 4px;
    padding: 3px 6px;
    color: #d6e4ff;
}
QLineEdit:focus {
    border-color: #00d4ff;
    background-color: #16305a;
}

/* 折叠区标题 */
QPushButton[class="collapsible"] {
    background: transparent;
    border: none;
    color: #9cc0ff;
    font-size: 9.5pt;
    font-weight: 700;
    padding: 2px 0;
    text-align: left;
}
QPushButton[class="collapsible"]:hover {
    color: #00d4ff;
}

QFrame[class="section"] {
    background: transparent;
    border: none;
}

/* 日志面板 */
QFrame[class="log-panel"] {
    background-color: #0a1628;
    border: 1px solid #1f3560;
    border-radius: 10px;
}

/* ===== 内容列表 ===== */
QListWidget[class="prompt-list"] {
    background-color: #0e1c33;
    border: 1px solid #1f3560;
    border-radius: 6px;
    padding: 2px;
    outline: 0;
}
QListWidget[class="prompt-list"]::item {
    padding: 6px 8px;
    border-radius: 4px;
    margin: 1px 0;
}

/* ===== 分栏手柄 ===== */
QSplitter::handle {
    background: transparent;
}
QSplitter::handle:hover {
    background: #1f3560;
}
QSplitter::handle:horizontal {
    width: 6px;
}
QSplitter::handle:vertical {
    height: 6px;
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
        self.setFixedSize(128, 84)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.dragging = False

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # 准星
        cx, cy = self.width() // 2, 30
        pen = QPen(QColor("#00d4ff"), 2)
        painter.setPen(pen)
        r = 12
        painter.drawEllipse(cx - r, cy - r, r * 2, r * 2)
        painter.drawLine(cx - r - 5, cy, cx + r + 5, cy)
        painter.drawLine(cx, cy - r - 5, cx, cy + r + 5)

        # 文字
        painter.setPen(QColor("#00d4ff"))
        font = painter.font()
        font.setPointSize(7)
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(self.rect().adjusted(0, 50, 0, -6), Qt.AlignmentFlag.AlignCenter, "按住拖向目标窗口")

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

    def __init__(self, hwnd, text, click_steps, key_steps, paste=True, lock_input=True, parent=None):
        super().__init__(parent)
        self.hwnd = hwnd
        self.text = text
        self.click_steps = click_steps
        self.key_steps = key_steps
        self.paste = paste
        self.lock_input = lock_input

    def run(self):
        try:
            ok = click_and_paste_send(self.hwnd, self.text, self.click_steps, self.key_steps,
                                      self.paste, self.lock_input)
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
        self.lock_input = bool(config.get('lock_input', True))
        self.paste = bool(config.get('paste', True))
        self.click_steps = list(config.get('click_steps') or [])
        self.key_steps = list(config.get('key_steps') or [])
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
                ok = click_and_paste_send(hwnd, compose_send_text(self.preface, self.pick_prompt()),
                                          self.click_steps, self.key_steps, self.paste,
                                          self.lock_input, self.isInterruptionRequested)
                self.log_signal.emit("✅ 发送成功。" if ok else "❌ 发送失败。")

                # 追加后续：延时指定时长后再发送
                if self.suffix.strip() and self.suffix_delay > 0:
                    self.log_signal.emit(f"🕒 {self.suffix_delay} 秒后发送追加后续...")
                    remaining = float(self.suffix_delay)
                    while remaining > 0 and not self.isInterruptionRequested():
                        time.sleep(min(0.2, remaining))
                        remaining -= 0.2
                    if not self.isInterruptionRequested():
                        ok2 = click_and_paste_send(hwnd, self.suffix, self.click_steps, self.key_steps,
                                                   self.paste, self.lock_input, self.isInterruptionRequested)
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
        # 可缩放窗口：默认 1180x780，最小 1040x640（分栏/日志区可拖拽调整）
        self.setMinimumSize(1040, 640)
        self.resize(1180, 780)

        self.worker = None
        self.next_fire_time = None  # 用于倒计时显示
        self._prompt_loading = False   # 内容编辑区程序化回填时抑制 textChanged 回写
        self._log_visible = True       # 日志面板展开状态
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

        self._refresh_schedule_preview()
        self._loading = False
        self._initializing = False
        self._pending_save = False
        if hasattr(self, '_debounce_timer'):
            self._debounce_timer.stop()

        # 恢复上次的窗口尺寸与分栏比例（不影响 ini 配置内容）
        self._restore_ui_state()

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
        root.setContentsMargins(12, 10, 12, 10)
        root.setSpacing(8)

        # ─── 顶部工具栏：身份 / 状态 / 时间 / 倒计时 / 配置操作 ───
        root.addWidget(self._build_toolbar())

        # ─── 主体：左右分栏（可拖拽）＋ 底部日志（可折叠） ───
        self.split_v = QSplitter(Qt.Orientation.Vertical)
        self.split_v.setChildrenCollapsible(False)
        self.split_v.setHandleWidth(6)

        self.split_h = QSplitter(Qt.Orientation.Horizontal)
        self.split_h.setChildrenCollapsible(False)
        self.split_h.setHandleWidth(6)
        self.split_h.addWidget(self._build_settings_panel())
        self.split_h.addWidget(self._build_content_panel())
        self.split_h.setStretchFactor(0, 0)
        self.split_h.setStretchFactor(1, 1)
        self.split_h.setSizes([450, 700])

        self.split_v.addWidget(self.split_h)
        self.split_v.addWidget(self._build_log_panel())
        self.split_v.setStretchFactor(0, 1)
        self.split_v.setStretchFactor(1, 0)
        self.split_v.setSizes([650, 130])

        root.addWidget(self.split_v, 1)

        # ─── 底部操作栏 ───
        root.addWidget(self._build_action_bar())

    # ── 工具栏 / 面板构建 ────────────────────────────────────

    def _build_toolbar(self):
        bar = QFrame()
        bar.setProperty("class", "toolbar")
        h = QHBoxLayout(bar)
        h.setContentsMargins(12, 8, 12, 8)
        h.setSpacing(10)

        title_lbl = QLabel("⏱️  定时发送")
        title_lbl.setProperty("class", "app-title")
        h.addWidget(title_lbl)

        self.status_pill = QLabel("● 待命中")
        self.status_pill.setProperty("class", "status-pill")
        self.status_pill.setProperty("state", "idle")
        h.addWidget(self.status_pill)

        h.addSpacing(6)
        self.lbl_now = QLabel()
        self.lbl_now.setProperty("class", "meta")
        h.addWidget(self.lbl_now)

        h.addSpacing(6)
        self.lbl_countdown = QLabel("--:--:--")
        self.lbl_countdown.setProperty("class", "chip")
        h.addWidget(self.lbl_countdown)

        self.lbl_countdown_label = QLabel("待命中 · 配置好后点「启动定时」")
        self.lbl_countdown_label.setProperty("class", "meta")
        h.addWidget(self.lbl_countdown_label)

        h.addStretch()

        for text, tip, slot in (
            ("💾 保存配置", "立即把当前配置写入本地配置文件", self._save_config_now),
            ("📥 导入配置", "从本地 ini 文件导入配置到当前窗口", self._import_config_file),
            ("♻️ 恢复出厂", "将当前窗口配置恢复为出厂默认（发送内容等全部清空）", self._reset_factory_config),
            ("🆕 新建窗口", "新开一个独立实例，用于管理另一个目标窗口", self._new_window),
        ):
            btn = QPushButton(text)
            btn.setProperty("class", "btn-mini")
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setToolTip(tip)
            btn.clicked.connect(slot)
            h.addWidget(btn)
        return bar

    def _build_settings_panel(self):
        """左栏：目标窗口 → 触发计划 → 发送行为，纵向可滚动，宽度可拖拽。"""
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setMinimumWidth(430)

        inner = QWidget()
        col = QVBoxLayout(inner)
        col.setContentsMargins(0, 0, 6, 0)
        col.setSpacing(10)

        target_card, target_layout, _ = self._card("目标窗口", "🎯")
        self._build_target_card(target_layout)
        col.addWidget(target_card)

        sched_card, sched_layout, _ = self._card("触发计划", "⏰")
        self._build_schedule_card(sched_layout)
        col.addWidget(sched_card, 1)

        behavior_card, behavior_layout, _ = self._card("发送行为", "⚙️")
        self._build_behavior_card(behavior_layout)
        col.addWidget(behavior_card)

        scroll.setWidget(inner)
        return scroll

    def _build_behavior_card(self, layout):
        self.chk_paste = QCheckBox("粘贴发送内容（Ctrl+V）")
        self.chk_paste.setChecked(True)
        self.chk_paste.setToolTip("关闭后不粘贴内容，只按下面的按键序列发送按键")
        self.chk_paste.toggled.connect(self._on_ui_changed_for_save)
        layout.addWidget(self.chk_paste)

        self.chk_lock_input = QCheckBox("发送前锁定鼠标键盘约 1 秒（避免与手动操作冲突）")
        self.chk_lock_input.setChecked(True)
        self.chk_lock_input.setToolTip("需管理员权限；未提权时自动跳过，不影响正常发送")
        self.chk_lock_input.toggled.connect(self._on_ui_changed_for_save)
        layout.addWidget(self.chk_lock_input)

        # 按键序列：粘贴完成后依次发送
        key_head = QHBoxLayout()
        key_head.setSpacing(5)
        key_title = QLabel("按键序列（粘贴后依次发送）")
        key_title.setProperty("class", "step-title")
        key_head.addWidget(key_title)
        key_head.addStretch()

        btn_add_key = QPushButton("＋ 添加")
        btn_add_key.setProperty("class", "btn-mini")
        btn_add_key.setToolTip("新增一个按键步骤，可填 Enter / Tab / Ctrl+A / F5 等")
        btn_add_key.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_add_key.clicked.connect(lambda: self._add_key_step())
        key_head.addWidget(btn_add_key)

        btn_clear_key = QPushButton("清空")
        btn_clear_key.setProperty("class", "btn-mini")
        btn_clear_key.setToolTip("清空全部按键：粘贴后不额外按键")
        btn_clear_key.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_clear_key.clicked.connect(self._clear_key_steps)
        key_head.addWidget(btn_clear_key)
        layout.addLayout(key_head)

        self.key_steps_layout = QVBoxLayout()
        self.key_steps_layout.setContentsMargins(0, 0, 0, 0)
        self.key_steps_layout.setSpacing(4)
        key_box = QWidget()
        key_box.setLayout(self.key_steps_layout)
        layout.addWidget(key_box)

        self.lbl_key_empty = QLabel("未设置按键：粘贴后不额外按键")
        self.lbl_key_empty.setProperty("class", "hint")
        layout.addWidget(self.lbl_key_empty)
        self.key_steps = []
        self._add_key_step('enter', 0)          # 默认回车，与旧版行为一致

        tip = QLabel("目标窗口若以管理员身份运行，本程序也需以管理员运行，否则键鼠消息会被系统拦截。")
        tip.setProperty("class", "hint")
        tip.setWordWrap(True)
        layout.addWidget(tip)

    def _build_action_bar(self):
        bar = QFrame()
        bar.setProperty("class", "actionbar")
        h = QHBoxLayout(bar)
        h.setContentsMargins(12, 8, 12, 8)
        h.setSpacing(10)

        self.btn_toggle = QPushButton("▶  启动定时")
        self.btn_toggle.setProperty("class", "btn-primary")
        self.btn_toggle.setMinimumHeight(42)
        self.btn_toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_toggle.clicked.connect(self.toggle_task)
        h.addWidget(self.btn_toggle, 2)

        btn_test = QPushButton("⚡  立即测试发送")
        btn_test.setProperty("class", "btn-secondary")
        btn_test.setMinimumHeight(42)
        btn_test.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_test.clicked.connect(self.test_trigger)
        h.addWidget(btn_test, 1)

        h.addStretch()

        self.lbl_save_state = QLabel("")
        self.lbl_save_state.setProperty("class", "meta")
        h.addWidget(self.lbl_save_state)
        return bar

    def _build_log_panel(self):
        panel = QFrame()
        panel.setProperty("class", "log-panel")
        v = QVBoxLayout(panel)
        v.setContentsMargins(10, 6, 10, 8)
        v.setSpacing(4)

        row = QHBoxLayout()
        row.setSpacing(8)
        self.btn_log_toggle = QPushButton("▾  运行日志")
        self.btn_log_toggle.setProperty("class", "collapsible")
        self.btn_log_toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_log_toggle.setToolTip("展开 / 收起运行日志")
        self.btn_log_toggle.clicked.connect(self._toggle_log)
        row.addWidget(self.btn_log_toggle)
        row.addStretch()

        btn_clear = QPushButton("清空日志")
        btn_clear.setProperty("class", "btn-mini")
        btn_clear.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_clear.clicked.connect(lambda: self.log_view.clear())
        row.addWidget(btn_clear)
        v.addLayout(row)

        self.log_view = QTextEdit()
        self.log_view.setProperty("class", "log-view")
        self.log_view.setReadOnly(True)
        self.log_view.setMinimumHeight(48)
        v.addWidget(self.log_view, 1)
        return panel

    def _toggle_log(self):
        self._log_visible = not self._log_visible
        self.log_view.setVisible(self._log_visible)
        self.btn_log_toggle.setText(("▾  " if self._log_visible else "▸  ") + "运行日志")
        sizes = self.split_v.sizes()
        if self._log_visible:
            self.split_v.setSizes([max(360, sum(sizes) - 130), 130])
        else:
            self.split_v.setSizes([max(400, sum(sizes) - 34), 34])

    def _collapsible_section(self, title, tip):
        """可折叠区块：返回 (容器, 内容布局, 标题按钮, 内容控件)。"""
        box = QFrame()
        box.setProperty("class", "section")
        v = QVBoxLayout(box)
        v.setContentsMargins(0, 2, 0, 0)
        v.setSpacing(6)

        head_row = QHBoxLayout()
        head_row.setSpacing(8)
        head = QPushButton("▸  " + title)
        head.setProperty("class", "collapsible")
        head.setCursor(Qt.CursorShape.PointingHandCursor)
        head.setCheckable(True)
        head_row.addWidget(head)

        tip_lbl = QLabel(tip)
        tip_lbl.setProperty("class", "hint")
        head_row.addWidget(tip_lbl)
        head_row.addStretch()
        v.addLayout(head_row)

        body_w = QWidget()
        body = QVBoxLayout(body_w)
        body.setContentsMargins(2, 0, 2, 0)
        body.setSpacing(6)
        body_w.setVisible(False)
        v.addWidget(body_w)

        head.toggled.connect(lambda on: self._toggle_section(head, body_w, title, on))
        return box, body, head, body_w

    @staticmethod
    def _toggle_section(head, body_w, title, on):
        head.setText(("▾  " if on else "▸  ") + title)
        body_w.setVisible(on)

    def _sync_optional_sections(self):
        """附加前言 / 追加后续若有内容则自动展开，避免内容被折叠隐藏。"""
        if self.preface_edit.toPlainText().strip():
            self._preface_head.setChecked(True)
        if self.suffix_edit.toPlainText().strip() or self.suffix_delay_spin.value() > 0:
            self._suffix_head.setChecked(True)

    # ── 窗口几何 / 分栏状态（存 QSettings，不污染 ini 配置） ──

    def _ui_settings(self):
        key = 'WindowsLoopSend' + ('-' + self.instance_name if self.instance_name else '')
        return QSettings('yezijinn', key)

    def _restore_ui_state(self):
        try:
            s = self._ui_settings()
            geo = s.value('geometry')
            if geo is not None:
                self.restoreGeometry(geo)
            sizes_h = s.value('split_h')
            if sizes_h and len(sizes_h) == 2:
                self.split_h.setSizes([int(x) for x in sizes_h])
            sizes_v = s.value('split_v')
            if sizes_v and len(sizes_v) == 2:
                self.split_v.setSizes([int(x) for x in sizes_v])
            log_visible = s.value('log_visible')
            if log_visible is not None and str(log_visible).lower() in ('false', '0'):
                self._toggle_log()      # 上次为收起态：沿用收起外观，避免日志被压成细条
        except Exception:
            pass

    def resizeEvent(self, event):
        """窄窗口下收起工具栏文字提示，避免时钟与按钮被挤压（倒计时胶囊始终保留）。"""
        super().resizeEvent(event)
        hint = getattr(self, 'lbl_countdown_label', None)
        if hint is not None:
            hint.setVisible(self.width() >= 1100)

    def _save_ui_state(self):
        try:
            s = self._ui_settings()
            s.setValue('geometry', self.saveGeometry())
            s.setValue('split_h', self.split_h.sizes())
            s.setValue('split_v', self.split_v.sizes())
            s.setValue('log_visible', self._log_visible)
        except Exception:
            pass

    def _build_target_card(self, layout):
        # 第一行：拖拽准星 ｜ 目标窗口选择
        row1 = QHBoxLayout()
        row1.setSpacing(10)

        self.picker_label = TargetPickerLabel()
        self.picker_label.targetCaptured.connect(self._on_target_captured)
        row1.addWidget(self.picker_label)

        right_col = QVBoxLayout()
        right_col.setSpacing(6)

        self.win_combo = QComboBox()
        self.win_combo.setMinimumWidth(120)
        # 窗口标题可能很长：宽度按最小可见字符数而非最长条目计算，避免撑破左栏
        self.win_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.win_combo.setMinimumContentsLength(10)
        self.win_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        btn_win_refresh = QPushButton("🔄")
        btn_win_refresh.setProperty("class", "btn-secondary")
        btn_win_refresh.setFixedWidth(40)
        btn_win_refresh.setToolTip("刷新可见窗口列表")
        btn_win_refresh.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_win_refresh.clicked.connect(self._refresh_window_list)

        combo_row = QHBoxLayout()
        combo_row.setSpacing(6)
        combo_row.addWidget(self.win_combo, 1)
        combo_row.addWidget(btn_win_refresh)
        right_col.addLayout(combo_row)
        self.win_combo.currentIndexChanged.connect(self._on_ui_changed_for_save)

        tip = QLabel("按住准星拖到目标输入框松开：既绑定窗口，也追加一个点击点")
        tip.setProperty("class", "hint")
        tip.setWordWrap(True)
        right_col.addWidget(tip)
        right_col.addStretch()

        row1.addLayout(right_col, 1)
        layout.addLayout(row1)

        # 第二块：点击序列（按顺序执行，每步自定义坐标与间隔）
        seq_head = QHBoxLayout()
        seq_head.setSpacing(5)
        seq_title = QLabel("点击序列（自上而下依次点击）")
        seq_title.setProperty("class", "step-title")
        seq_head.addWidget(seq_title)
        seq_head.addStretch()

        btn_add_click = QPushButton("＋ 添加")
        btn_add_click.setProperty("class", "btn-mini")
        btn_add_click.setToolTip("新增一个点击点（默认取上一个点的坐标，可手动改）")
        btn_add_click.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_add_click.clicked.connect(lambda: self._add_click_step())
        seq_head.addWidget(btn_add_click)

        btn_clear_click = QPushButton("清空")
        btn_clear_click.setProperty("class", "btn-mini")
        btn_clear_click.setToolTip("清空全部点击点：只聚焦目标窗口，不点击")
        btn_clear_click.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_clear_click.clicked.connect(self._clear_click_steps)
        seq_head.addWidget(btn_clear_click)
        layout.addLayout(seq_head)

        self.click_steps_layout = QVBoxLayout()
        self.click_steps_layout.setContentsMargins(0, 0, 0, 0)
        self.click_steps_layout.setSpacing(4)
        click_box = QWidget()
        click_box.setLayout(self.click_steps_layout)
        layout.addWidget(click_box)

        self.lbl_click_empty = QLabel("未设置点击点：发送时只聚焦窗口，不点击")
        self.lbl_click_empty.setProperty("class", "hint")
        layout.addWidget(self.lbl_click_empty)
        self.click_steps = []

    # ── 步骤序列编辑（点击序列 / 按键序列） ──────────────────

    @staticmethod
    def _step_row():
        """一个步骤行的容器与布局。"""
        frame = QFrame()
        frame.setProperty("class", "step-row")
        row = QHBoxLayout(frame)
        row.setContentsMargins(6, 4, 6, 4)
        row.setSpacing(4)
        return frame, row

    @staticmethod
    def _step_buttons(row, entry, on_up, on_down, on_del):
        """步骤行尾部的上移 / 下移 / 删除按钮（闭包捕获 entry 字典本体）。"""
        for text, tip, slot, cls in (
            ("↑", "上移（越靠前越先执行）", lambda: on_up(entry), "btn-step"),
            ("↓", "下移（越靠后越晚执行）", lambda: on_down(entry), "btn-step"),
            ("✕", "删除该步骤", lambda: on_del(entry), "btn-step-del"),
        ):
            btn = QPushButton(text)
            btn.setProperty("class", cls)
            btn.setFixedSize(22, 22)
            btn.setToolTip(tip)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(slot)
            row.addWidget(btn)

    def _add_click_step(self, x=None, y=None, delay_ms=0, index=None):
        """新增一个点击点。x/y 为空时沿用上一点坐标；一个都没有则取当前光标位置。"""
        if x is None and y is None:
            if self.click_steps:
                x = self.click_steps[-1]['x'].value()
                y = self.click_steps[-1]['y'].value()
            else:
                pt = wintypes.POINT()
                user32.GetCursorPos(ctypes.byref(pt))
                x, y = pt.x, pt.y

        entry = {}
        frame, row = self._step_row()
        num = QLabel("")
        num.setFixedWidth(16)
        num.setProperty("class", "meta")
        # 坐标由准星捕获或直接输入，不需要步进箭头；去掉箭头才能完整显示五位坐标
        sp_x = QSpinBox()
        sp_x.setRange(-32767, 32767)
        sp_x.setPrefix("X ")
        sp_x.setValue(int(x))
        sp_x.setMinimumWidth(78)
        sp_x.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        sp_y = QSpinBox()
        sp_y.setRange(-32767, 32767)
        sp_y.setPrefix("Y ")
        sp_y.setValue(int(y))
        sp_y.setMinimumWidth(78)
        sp_y.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        sp_d = QSpinBox()
        sp_d.setRange(0, 3600000)
        sp_d.setSuffix(" ms")
        sp_d.setValue(int(delay_ms))
        sp_d.setMinimumWidth(86)
        sp_d.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        sp_d.setToolTip("执行这个点击点之前等待的时长（毫秒）")

        row.addWidget(num)
        row.addWidget(sp_x, 1)
        row.addWidget(sp_y, 1)
        row.addWidget(sp_d)
        self._step_buttons(
            row, entry,
            lambda e: self._move_step(self.click_steps, self.click_steps_layout, e, -1),
            lambda e: self._move_step(self.click_steps, self.click_steps_layout, e, 1),
            lambda e: self._remove_step(self.click_steps, self.click_steps_layout, e))
        entry.update({'frame': frame, 'num': num, 'x': sp_x, 'y': sp_y, 'delay': sp_d})

        for widget in (sp_x, sp_y, sp_d):
            widget.valueChanged.connect(self._on_ui_changed_for_save)
        pos = len(self.click_steps) if index is None else max(0, min(len(self.click_steps), index))
        self.click_steps.insert(pos, entry)
        self.click_steps_layout.insertWidget(pos, frame)
        self._refresh_step_numbers()
        self._on_ui_changed_for_save()
        return entry

    def _add_key_step(self, key='enter', delay_ms=0, index=None):
        """新增一个按键步骤。"""
        entry = {}
        frame, row = self._step_row()
        num = QLabel("")
        num.setFixedWidth(16)
        num.setProperty("class", "meta")
        edit = QLineEdit(str(key))
        edit.setPlaceholderText("如 Enter / Tab / Ctrl+A / F5")
        edit.setMinimumWidth(92)
        edit.setToolTip("单个按键或组合键（用 + 连接表示同时按下）")
        sp_d = QSpinBox()
        sp_d.setRange(0, 3600000)
        sp_d.setSuffix(" ms")
        sp_d.setValue(int(delay_ms))
        sp_d.setMinimumWidth(86)
        sp_d.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        sp_d.setToolTip("发送这个按键之前等待的时长（毫秒）")

        row.addWidget(num)
        row.addWidget(edit, 1)
        row.addWidget(sp_d)
        self._step_buttons(
            row, entry,
            lambda e: self._move_step(self.key_steps, self.key_steps_layout, e, -1),
            lambda e: self._move_step(self.key_steps, self.key_steps_layout, e, 1),
            lambda e: self._remove_step(self.key_steps, self.key_steps_layout, e))
        entry.update({'frame': frame, 'num': num, 'edit': edit, 'delay': sp_d})

        edit.textChanged.connect(self._on_ui_changed_for_save)
        sp_d.valueChanged.connect(self._on_ui_changed_for_save)
        pos = len(self.key_steps) if index is None else max(0, min(len(self.key_steps), index))
        self.key_steps.insert(pos, entry)
        self.key_steps_layout.insertWidget(pos, frame)
        self._refresh_step_numbers()
        self._on_ui_changed_for_save()
        return entry

    def _move_step(self, steps, layout, entry, delta):
        """在序列中上移/下移一个步骤，界面与数据同步换位。"""
        i = steps.index(entry)
        j = i + delta
        if not 0 <= j < len(steps):
            return
        steps[i], steps[j] = steps[j], steps[i]
        frame = entry['frame']
        layout.removeWidget(frame)
        layout.insertWidget(j, frame)
        self._refresh_step_numbers()
        self._on_ui_changed_for_save()

    def _remove_step(self, steps, layout, entry):
        if entry not in steps:
            return
        steps.remove(entry)
        layout.removeWidget(entry['frame'])
        entry['frame'].deleteLater()
        self._refresh_step_numbers()
        self._on_ui_changed_for_save()

    def _clear_click_steps(self):
        for entry in list(self.click_steps):
            self._remove_step(self.click_steps, self.click_steps_layout, entry)

    def _clear_key_steps(self):
        for entry in list(self.key_steps):
            self._remove_step(self.key_steps, self.key_steps_layout, entry)

    def _refresh_step_numbers(self):
        """重排两个序列的序号，并按空列表状态显示提示。"""
        for i, entry in enumerate(getattr(self, 'click_steps', []), 1):
            entry['num'].setText("%d." % i)
        for i, entry in enumerate(getattr(self, 'key_steps', []), 1):
            entry['num'].setText("%d." % i)
        empty_click = getattr(self, 'lbl_click_empty', None)
        if empty_click is not None:
            empty_click.setVisible(not self.click_steps)
        empty_key = getattr(self, 'lbl_key_empty', None)
        if empty_key is not None:
            empty_key.setVisible(not self.key_steps)

    def _get_click_steps(self):
        return [{'x': e['x'].value(), 'y': e['y'].value(), 'delay_ms': e['delay'].value()}
                for e in self.click_steps]

    def _set_click_steps(self, steps):
        for entry in list(self.click_steps):
            self.click_steps_layout.removeWidget(entry['frame'])
            entry['frame'].deleteLater()
        self.click_steps = []
        for step in steps or []:
            self._add_click_step(step.get('x', 0), step.get('y', 0), step.get('delay_ms', 0))
        self._refresh_step_numbers()

    def _get_key_steps(self):
        return [{'key': e['edit'].text().strip(), 'delay_ms': e['delay'].value()}
                for e in self.key_steps if e['edit'].text().strip()]

    def _set_key_steps(self, steps):
        for entry in list(self.key_steps):
            self.key_steps_layout.removeWidget(entry['frame'])
            entry['frame'].deleteLater()
        self.key_steps = []
        for step in steps or []:
            self._add_key_step(step.get('key', 'enter'), step.get('delay_ms', 0))
        self._refresh_step_numbers()

    def _build_content_panel(self):
        """右栏：发送内容工作台 —— 列表（顺序即发送顺序）＋ 编辑器 ＋ 可折叠附加文本。"""
        card, layout, _ = self._card("发送内容", "📝")

        # ── 工具条：增删改排序 + 导入 + 发送方案 ──
        tb = QHBoxLayout()
        tb.setSpacing(5)

        def _mini(text, tip, slot):
            btn = QPushButton(text)
            btn.setProperty("class", "btn-mini")
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setToolTip(tip)
            btn.clicked.connect(slot)
            tb.addWidget(btn)
            return btn

        _mini("➕ 新增", "新增一条发送内容", lambda: self._add_prompt(''))
        _mini("⧉ 复制", "在下方复制当前选中内容", self._duplicate_prompt)
        _mini("🗑 删除", "删除当前选中内容", self._delete_prompt)
        _mini("⬆", "上移（决定按顺序发送时的先后）", lambda: self._move_prompt(-1))
        _mini("⬇", "下移（决定按顺序发送时的先后）", lambda: self._move_prompt(1))
        _mini("📂 导入", "导入 .txt / .md，用 --- 分隔多条发送内容", self._import_send_file)

        tb.addSpacing(6)
        self.combo_strategy = QComboBox()
        self.combo_strategy.addItem("顺序发送")
        self.combo_strategy.addItem("随机发送")
        self.combo_strategy.setToolTip("多条内容的发送方案：按顺序轮转 / 每次随机抽取一条")
        self.combo_strategy.currentIndexChanged.connect(self._on_ui_changed_for_save)
        tb.addWidget(self.combo_strategy)

        tb.addStretch()
        layout.addLayout(tb)

        # ── 主体：内容列表 ｜ 编辑区（可拖拽分隔） ──
        self.prompt_list = QListWidget()
        self.prompt_list.setProperty("class", "prompt-list")
        self.prompt_list.setMinimumWidth(230)
        # 摘要超长时省略号截断，不出现横向滚动条（完整首行与字数见条目提示）
        self.prompt_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.prompt_list.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.prompt_list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.prompt_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.prompt_list.setToolTip("选中一条后即可在右侧编辑；可直接拖拽调整顺序")
        self.prompt_list.currentRowChanged.connect(self._on_prompt_row_changed)
        self.prompt_list.model().rowsMoved.connect(self._on_prompt_rows_moved)

        self.prompt_edit = QPlainTextEdit()
        self.prompt_edit.setPlaceholderText("在此编辑选中内容（可多行，整块作为一条发送）")
        self.prompt_edit.textChanged.connect(self._on_prompt_text_changed)

        # 列表 + 统计脚注（条目数与字数紧随内容列表）
        list_col = QWidget()
        list_col_layout = QVBoxLayout(list_col)
        list_col_layout.setContentsMargins(0, 0, 0, 0)
        list_col_layout.setSpacing(4)
        list_col_layout.addWidget(self.prompt_list, 1)
        self.lbl_prompt_count = QLabel("0 条")
        self.lbl_prompt_count.setProperty("class", "meta")
        self.lbl_prompt_count.setAlignment(Qt.AlignmentFlag.AlignRight)
        list_col_layout.addWidget(self.lbl_prompt_count)

        self.split_prompt = QSplitter(Qt.Orientation.Horizontal)
        self.split_prompt.setChildrenCollapsible(False)
        self.split_prompt.setHandleWidth(6)
        self.split_prompt.addWidget(list_col)
        self.split_prompt.addWidget(self.prompt_edit)
        self.split_prompt.setStretchFactor(0, 0)
        self.split_prompt.setStretchFactor(1, 1)
        self.split_prompt.setSizes([260, 480])
        layout.addWidget(self.split_prompt, 1)

        # ── 附加前言（可选，折叠） ──
        box, body, head, _ = self._collapsible_section("附加前言", "每次发送时自动置于每条内容开头")
        self.preface_edit = QPlainTextEdit()
        self.preface_edit.setPlaceholderText("（可选）在每条发送内容的开头附加这一份相同内容...")
        self.preface_edit.setFixedHeight(int(self.preface_edit.fontMetrics().lineSpacing() * 3) + 12)
        self.preface_edit.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.preface_edit.textChanged.connect(self._on_ui_changed_for_save)
        body.addWidget(self.preface_edit)
        layout.addWidget(box)
        self._preface_head = head

        # ── 追加后续（可选，折叠） ──
        box, body, head, _ = self._collapsible_section("追加后续", "每条主内容发送后，延时再补发一段")
        self.suffix_edit = QPlainTextEdit()
        self.suffix_edit.setPlaceholderText("（可选）每一次发送主内容之后 再延迟追加的后续内容...")
        self.suffix_edit.setFixedHeight(int(self.suffix_edit.fontMetrics().lineSpacing() * 3) + 12)
        self.suffix_edit.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.suffix_edit.textChanged.connect(self._on_ui_changed_for_save)
        body.addWidget(self.suffix_edit)

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
        hint = QLabel("循环模式下不得超过任务间隔")
        hint.setProperty("class", "hint")
        delay_row.addWidget(hint)
        delay_row.addStretch()
        body.addLayout(delay_row)
        layout.addWidget(box)
        self._suffix_head = head

        return card

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

        # 「获取此时」：把当前激活面板的触发时间设为按下这一刻
        self.btn_get_now = QPushButton("🕐 获取此时")
        self.btn_get_now.setProperty("class", "btn-now")
        self.btn_get_now.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_get_now.setToolTip("把触发时间设为按下按钮的当前时刻")
        self.btn_get_now.clicked.connect(self._apply_now_time)
        seg_row.addSpacing(8)
        seg_row.addWidget(self.btn_get_now)

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
        self.loop_start_dt.setMinimumHeight(42)
        self.loop_start_dt.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        self.loop_start_dt.setStyleSheet("QDateTimeEdit { font-size: 12pt; font-weight: 600; }")
        lp.addWidget(self.loop_start_dt)

        # 结束
        self.loop_end_dt = QDateTimeEdit(QDateTime.currentDateTime().addDays(7))
        self.loop_end_dt.setDisplayFormat("yyyy-MM-dd  HH:mm:ss")
        self.loop_end_dt.setCalendarPopup(True)
        self.loop_end_dt.setMinimumHeight(42)
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
        self.spin_interval.setMinimumHeight(42)
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
        self.list_schedule.setMinimumHeight(76)
        self.list_schedule.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        # 触发时间条目宽度不参与 sizeHint，超长时省略号截断，避免撑宽左栏
        self.list_schedule.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list_schedule.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.list_schedule.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
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

    # ── 模式切换 ─────────────────────────────────────────────

    def _set_mode(self, mode):
        is_single = (mode == "single")
        self.btn_seg_single.setProperty("active", is_single)
        self.btn_seg_loop.setProperty("active", not is_single)
        # 同步 Qt 勾选态：排他按钮组下程序化切换（配置加载/热更新）不会自动勾选，
        # 不同步会出现「视觉已切换、键盘与勾选语义仍停在旧模式」的不一致
        self.btn_seg_single.setChecked(is_single)
        self.btn_seg_loop.setChecked(not is_single)
        self.btn_seg_single.style().unpolish(self.btn_seg_single)
        self.btn_seg_single.style().polish(self.btn_seg_single)
        self.btn_seg_loop.style().unpolish(self.btn_seg_loop)
        self.btn_seg_loop.style().polish(self.btn_seg_loop)

        self.single_panel.setVisible(is_single)
        self.loop_panel.setVisible(not is_single)
        self._sync_suffix_limit()
        self._refresh_schedule_preview()
        self._on_ui_changed_for_save()

    def _apply_now_time(self):
        """「获取此时」：把当前激活面板的触发时间设为按下这一刻。"""
        now = QDateTime.currentDateTime()
        if self.btn_seg_single.property("active"):
            self.single_dt.setDateTime(now)
            self._append_log(f"🕐 单次触发时间已设为当前时刻：{now.toString('yyyy-MM-dd HH:mm:ss')}")
        else:
            self.loop_start_dt.setDateTime(now)
            # 开始时间取当下后，若结束时间已不在未来则同步顺延，避免出现「开始晚于结束」的无效配置
            if self.loop_end_dt.dateTime() <= now:
                self.loop_end_dt.setDateTime(now.addDays(1))
                self._append_log("🕐 循环开始时间已设为当前时刻；原结束时间不晚于开始，已自动顺延 1 天。")
            else:
                self._append_log(f"🕐 循环开始时间已设为当前时刻：{now.toString('yyyy-MM-dd HH:mm:ss')}")

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

    # ── 配置文件：持久化 + 热更新 ─────────────────────────────

    def _on_ui_changed_for_save(self, *_):
        """任何 UI 改动 → 立即持久化（含热更新的本地回写）。"""
        if self._is_loading():
            return
        # 用户在界面上真实改动过；程序回填（初始化/热更新）在 _is_loading 时已提前 return
        self._user_touched = True
        # 运行中的任务使用启动时的配置快照，界面改动不会即时影响本次运行，首次改动时明确告知
        if self.worker and self.worker.isRunning():
            if not getattr(self, '_run_edit_notified', False):
                self._run_edit_notified = True
                self._append_log("ℹ️  任务运行中：本次改动已保存，将在下次启动定时时生效。")
        else:
            self._run_edit_notified = False
        self._dirty = True
        if hasattr(self, 'lbl_save_state'):
            self.lbl_save_state.setText("● 未保存…")
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
            'lock_input': self.chk_lock_input.isChecked(),
            'paste': self.chk_paste.isChecked(),
            'click_steps': self._get_click_steps(),
            'key_steps': self._get_key_steps(),
            # 旧键保留：仅记录首个点击点，便于旧版本或外部工具仍能读到坐标
            'click_x': self.click_steps[0]['x'].value() if self.click_steps else 0,
            'click_y': self.click_steps[0]['y'].value() if self.click_steps else 0,
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
            if hasattr(self, 'lbl_save_state'):
                self.lbl_save_state.setText("已保存 " + datetime.datetime.now().strftime('%H:%M:%S'))
        except Exception as e:
            # 写盘失败：保持 dirty，避免误判已保存而失去对未落盘编辑的保护
            self._dirty = True
            if hasattr(self, 'lbl_save_state'):
                self.lbl_save_state.setText("● 保存失败")
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
        self.chk_lock_input.setChecked(bool(cfg.get('lock_input', True)))
        self.chk_paste.setChecked(bool(cfg.get('paste', True)))

        # 点击 / 按键序列：优先读新键；旧配置只有单点坐标时迁移成一个点击点
        click_steps = cfg.get('click_steps') or []
        if not click_steps:
            old_x, old_y = int(cfg.get('click_x', 0) or 0), int(cfg.get('click_y', 0) or 0)
            if old_x != 0 or old_y != 0:
                click_steps = [{'x': old_x, 'y': old_y, 'delay_ms': 0}]
        self._set_click_steps(click_steps)

        key_steps = cfg.get('key_steps') or []
        if not key_steps:
            key_steps = [{'key': 'enter', 'delay_ms': 0}]      # 旧配置默认回车
        self._set_key_steps(key_steps)

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
        self.chk_lock_input.setChecked(bool(cfg.get('lock_input', True)))
        self._sync_optional_sections()

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
        """用给定列表重建内容工作台（顺序即发送顺序，至少保留一条）。"""
        self.prompt_list.clear()
        for text in (prompts or ['']):
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, text or '')
            self.prompt_list.addItem(item)
        if self.prompt_list.count() == 0:
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, '')
            self.prompt_list.addItem(item)
        self._refresh_prompt_items()
        self.prompt_list.setCurrentRow(0)
        self._update_prompt_count()

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
        self._user_touched = True   # 导入是用户显式动作，须防止附属窗口关闭时被当作未使用实例清空
        self._append_log(f"📂  已导入 {len(prompts)} 条发送内容（来自 {os.path.basename(path)}）")
        self._persist()

    # ── 窗口列表 ─────────────────────────────────────────────

    def _refresh_window_list(self):
        prev_hwnd = self.win_combo.currentData()
        prev_title = self.win_combo.currentText()
        was_loading = self._loading
        # 刷新属程序化重建，不应被判定为用户改动而触发保存
        self._loading = True
        stale = False
        try:
            self.win_combo.clear()
            for title, hwnd in list_all_windows():
                self.win_combo.addItem(f"{title}", hwnd)

            # 按 hwnd 恢复原选中目标，避免仅因刷新而丢失已配置的目标窗口
            restore = -1
            if prev_hwnd:
                for i in range(self.win_combo.count()):
                    if self.win_combo.itemData(i) == prev_hwnd:
                        restore = i
                        break
            if restore < 0 and prev_title:
                idx = self.win_combo.findText(prev_title)
                if idx >= 0:
                    restore = idx
            if restore < 0 and prev_title:
                # 目标窗口已关闭：保留原条目（句柄失效会在启动/测试时明确提示），
                # 而不是静默改选到列表首位的其它窗口
                self.win_combo.insertItem(0, prev_title, prev_hwnd)
                restore = 0
                stale = True
            if restore >= 0:
                self.win_combo.setCurrentIndex(restore)
        finally:
            self._loading = was_loading
        if stale:
            self._append_log(f"⚠️  原目标窗口「{prev_title}」已不存在，请重新拖拽捕获。")

    def _on_target_captured(self, hwnd, x, y, title):
        if not hwnd:
            self._append_log("⚠️  未捕获到有效窗口，请把准星拖到目标窗口内部再松开。")
            return

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

        # 捕获点直接追加为点击序列的下一步：拖几次即可得到多段点击
        self._add_click_step(x, y, 0)

        # 捕获是用户显式动作：即使坐标/选中项未变化也要标记改动并持久化，
        # 否则附属窗口关闭时会被当作「未使用实例」静默删除配置
        self._on_ui_changed_for_save()
        self._append_log(f"🎯 已捕获目标 — 窗口：{title}，坐标：({x}, {y})，"
                         f"已作为第 {len(self.click_steps)} 个点击点")

    # ── 发送内容：列表 + 编辑器 ──────────────────────────────

    @staticmethod
    def _prompt_summary(idx, text):
        """列表摘要：编号 + 首行（超长截断）。完整首行与字数见条目提示。"""
        lines = text.strip().splitlines()
        first = lines[0].strip() if lines else ''
        if len(first) > 16:
            first = first[:16] + "…"
        return f"{idx}. {first or '（空）'}"

    @staticmethod
    def _prompt_item_text(item):
        return item.data(Qt.ItemDataRole.UserRole) or ''

    def _apply_prompt_item(self, item, idx):
        """把编号 / 摘要 / 提示写到单个条目（内容不变）。"""
        text = self._prompt_item_text(item)
        item.setText(self._prompt_summary(idx, text))
        if not text.strip():
            item.setToolTip("（空内容，发送时自动跳过）")
            return
        lines = text.strip().splitlines()
        head = lines[0].strip()
        item.setToolTip((head[:80] + ("…" if len(head) > 80 else "")) + f"\n共 {len(text)} 字")

    def _refresh_prompt_items(self):
        """按当前顺序重建编号、摘要与提示（不改动条目内容与顺序）。"""
        for i in range(self.prompt_list.count()):
            self._apply_prompt_item(self.prompt_list.item(i), i + 1)

    def _add_prompt(self, text=''):
        """新增一条内容并选中它（空内容也允许，发送时自动跳过）。"""
        item = QListWidgetItem()
        item.setData(Qt.ItemDataRole.UserRole, text)
        self.prompt_list.addItem(item)
        self._refresh_prompt_items()
        self.prompt_list.setCurrentItem(item)
        self._update_prompt_count()
        self._on_ui_changed_for_save()
        return item

    def _delete_prompt(self):
        row = self.prompt_list.currentRow()
        if row < 0:
            return
        self.prompt_list.takeItem(row)
        if self.prompt_list.count() == 0:
            self._set_prompts([''])   # 工作台不留空白，始终保留一条待编辑
            self._on_ui_changed_for_save()
            return
        self.prompt_list.setCurrentRow(min(row, self.prompt_list.count() - 1))
        self._refresh_prompt_items()
        self._update_prompt_count()
        self._on_ui_changed_for_save()

    def _duplicate_prompt(self):
        row = self.prompt_list.currentRow()
        if row < 0:
            return
        item = QListWidgetItem()
        item.setData(Qt.ItemDataRole.UserRole, self._prompt_item_text(self.prompt_list.item(row)))
        self.prompt_list.insertItem(row + 1, item)
        self.prompt_list.setCurrentItem(item)
        self._refresh_prompt_items()
        self._update_prompt_count()
        self._on_ui_changed_for_save()

    def _move_prompt(self, delta):
        """上移 / 下移当前条目（决定按顺序发送时的先后）。"""
        row = self.prompt_list.currentRow()
        target = row + delta
        if row < 0 or not 0 <= target < self.prompt_list.count():
            return
        item = self.prompt_list.takeItem(row)
        self.prompt_list.insertItem(target, item)
        self.prompt_list.setCurrentItem(item)
        self._refresh_prompt_items()
        self._on_ui_changed_for_save()

    def _on_prompt_row_changed(self, row):
        """切换选中条目：把内容回填到编辑区（抑制回写）。"""
        if not 0 <= row < self.prompt_list.count():
            return
        self._prompt_loading = True
        try:
            self.prompt_edit.setPlainText(self._prompt_item_text(self.prompt_list.item(row)))
        finally:
            self._prompt_loading = False

    def _on_prompt_text_changed(self):
        """编辑区改动 → 实时回写当前条目（摘要/统计/配置同步更新）。"""
        if self._prompt_loading:
            return
        item = self.prompt_list.currentItem()
        if item is None:
            return
        text = self.prompt_edit.toPlainText()
        item.setData(Qt.ItemDataRole.UserRole, text)
        self._apply_prompt_item(item, self.prompt_list.currentRow() + 1)
        self._update_prompt_count()
        self._on_ui_changed_for_save()

    def _on_prompt_rows_moved(self, *_):
        """拖拽排序后重排编号并持久化。"""
        self._refresh_prompt_items()
        self._on_ui_changed_for_save()

    def get_prompt_list(self):
        return [self._prompt_item_text(self.prompt_list.item(i)).strip()
                for i in range(self.prompt_list.count())
                if self._prompt_item_text(self.prompt_list.item(i)).strip()]

    def _update_prompt_count(self):
        texts = [self._prompt_item_text(self.prompt_list.item(i)).strip()
                 for i in range(self.prompt_list.count())]
        texts = [t for t in texts if t]
        if not texts:
            self.lbl_prompt_count.setText("0 条")
            return
        self.lbl_prompt_count.setText(f"{len(texts)} 条 · {sum(len(t) for t in texts)} 字")

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
            if fire_dt < now - datetime.timedelta(seconds=PAST_TOLERANCE_SEC):
                item = QListWidgetItem(f"⚠️  {fire_dt.strftime('%Y-%m-%d %H:%M:%S')}  （时间已过去，启动时会询问是否顺延）")
                item.setForeground(QColor("#ffaa3d"))
                self.list_schedule.addItem(item)
            else:
                # 落在容忍窗口内（例如刚点「获取此时」）按即将触发呈现，与启动行为一致
                label = "即将触发" if fire_dt <= now else "单次 · 仅 1 次"
                item = QListWidgetItem(f"🕐  {fire_dt.strftime('%Y-%m-%d %H:%M:%S')}    {label}")
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
            if not for_test and start_dt < now - datetime.timedelta(seconds=PAST_TOLERANCE_SEC):
                reply = QMessageBox.question(
                    self, "时间已过去",
                    f"所设单次时间 {start_dt.strftime('%Y-%m-%d %H:%M:%S')} 已过去。\n是否自动顺延至下一个同点时刻？",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
                )
                if reply == QMessageBox.StandardButton.Yes:
                    # 原时间可能已过去多日，仅加 1 天仍可能落在过去，故顺延到第一个未来同点
                    while start_dt <= now:
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
            # 结束时间之前已无可用触发时刻时直接拦截，避免启动后立即「无任务可执行」结束
            if not compute_fire_list('loop', start_dt, end_dt, self.spin_interval.value(),
                                     self.chk_immediate.isChecked(), max_count=1):
                QMessageBox.warning(
                    self, "配置错误",
                    "按当前开始/结束时间与间隔，结束时间之前已无可用触发时刻。\n请调整时间或间隔后重试。")
                return None

        # 按键序列先校验按键名，避免运行到一半才发现无法识别
        key_steps = self._get_key_steps()
        bad_keys = [s['key'] for s in key_steps if parse_key_combo(s['key']) is None]
        if bad_keys:
            QMessageBox.warning(self, "配置错误",
                                "以下按键无法识别：%s\n请改成 Enter / Tab / Ctrl+A / F5 这类写法。"
                                % '、'.join(bad_keys))
            return None

        return {
            'hwnd': hwnd,
            'prompts': prompts,
            'strategy': 'random' if self.combo_strategy.currentIndex() == 1 else 'sequence',
            'click_steps': self._get_click_steps(),
            'key_steps': key_steps,
            'paste': self.chk_paste.isChecked(),
            'mode': 'single' if is_single else 'loop',
            'start_dt': start_dt,
            'end_dt': end_dt,
            'interval_min': self.spin_interval.value(),
            'run_immediately': self.chk_immediate.isChecked(),
            'lock_input': self.chk_lock_input.isChecked(),
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
            # 停止：用局部引用等待，避免 processEvents 期间 finished 槽把 self.worker 置空后
            # 循环条件再访问 None 抛 AttributeError
            worker = self.worker
            worker.requestInterruption()
            while not worker.wait(100):
                QApplication.processEvents()
            if self.worker is worker:
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
        # 引用释放在 finished（线程真正结束）后执行，避免 run() 尚未退出时被析构
        self.worker.finished.connect(self._on_worker_finished)
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
            # 只更新界面；线程对象留到 finished 再释放。
            # run() 内 emit("done") 后线程仍在执行，此刻置空 self.worker 会让
            # QThread 在运行中被析构（QThread: Destroyed while thread is still running）。
            self._set_status("done", "● 已完成")
            self.btn_toggle.setText("▶  启动定时")
            self.btn_toggle.setProperty("class", "btn-primary")
            self.btn_toggle.style().unpolish(self.btn_toggle)
            self.btn_toggle.style().polish(self.btn_toggle)
            self.next_fire_time = None

    def _on_worker_finished(self):
        """线程真正结束后释放引用；若期间已启动新任务则不动当前 worker。"""
        if self.sender() is self.worker:
            self.worker = None
            self.next_fire_time = None
            if not self._is_loading():
                self._refresh_schedule_preview()

    def _on_next_fire(self, nxt):
        self.next_fire_time = nxt

    def _set_status(self, state, text):
        self.status_pill.setText(text)
        self.status_pill.setProperty("state", state)
        self.status_pill.style().unpolish(self.status_pill)
        self.status_pill.style().polish(self.status_pill)

    # ── 测试 ────────────────────────────────────────────────

    def test_trigger(self):
        # 互斥守卫前置：已有测试在发送时直接拒绝，不再走配置校验弹框
        if getattr(self, '_test_busy', False):
            self._append_log("⚠️  已有一次测试发送进行中，请稍候再试。")
            return
        cfg = self._collect_config(for_test=True)
        if not cfg:
            return
        self._append_log("⚡  执行测试发送...")
        prompts = cfg['prompts']
        prompt = random.choice(prompts) if cfg['strategy'] == 'random' else prompts[0]
        text = compose_send_text(cfg.get('preface', ''), prompt)
        # 后台线程发送，防止阻塞 GUI（发送含提前 1s 锁输入 + 键鼠模拟，内部互斥排队最坏数秒）
        self._test_busy = True
        self._test_thread = TestSendThread(cfg['hwnd'], text, cfg['click_steps'], cfg['key_steps'],
                                           cfg.get('paste', True), cfg.get('lock_input', True))
        self._test_thread.done_signal.connect(self._on_test_done)
        # 线程引用同样在 finished 后释放，避免 run() 未退出时被析构
        self._test_thread.finished.connect(self._on_test_finished)
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

    def _on_test_finished(self):
        if self.sender() is getattr(self, '_test_thread', None):
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
            worker = self.worker
            worker.requestInterruption()
            while not worker.wait(100):
                QApplication.processEvents()
            if self.worker is worker:
                self.worker = None
        tt = getattr(self, '_test_thread', None)
        if tt and tt.isRunning():
            # 测试发送内含最长约 30s 的发送互斥等待，轮询等待其结束，避免孤儿线程残留
            waited = 0
            while tt.isRunning() and waited < 35000:
                tt.wait(200)
                waited += 200
                QApplication.processEvents()
        # 先落盘未保存的编辑（防抖窗口内的改动），避免默认窗口关闭时丢失配置
        self._flush_before_close()
        if not self._cleanup_tmp_config():
            event.ignore()
            return
        self._save_ui_state()
        event.accept()

    def _flush_before_close(self):
        """关闭前把防抖窗口内尚未落盘的编辑写入配置，避免丢失刚改的内容。
        默认窗口原先直接跳过清理分支，导致最后 400ms 内的改动被丢弃。"""
        timer = getattr(self, '_debounce_timer', None)
        if timer is not None:
            timer.stop()
        if getattr(self, '_pending_save', False) or getattr(self, '_dirty', False):
            try:
                self._flush_pending_save()
            except Exception as e:
                print(f"[关闭前保存失败] {e}")

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
        self._user_touched = True   # 导入是用户显式动作，须防止附属窗口关闭时被当作未使用实例清空
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
            'lock_input': True,
            'paste': True,
            'click_x': 0,
            'click_y': 0,
            'click_steps': [],
            'key_steps': [{'key': 'enter', 'delay_ms': 0}],
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
