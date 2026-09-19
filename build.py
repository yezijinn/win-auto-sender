#!/usr/bin/env python
"""WindowsLoopSend 一键打包脚本（Python 版）。

用法：python build.py
说明：
  - 自动在候选解释器中探测可用的 PyInstaller，优先使用项目固定 Python 3.12，
    找不到则回退到当前解释器；
  - 调用 PyInstaller 生成单文件 WindowsLoopSend.exe（已内嵌 Python 与全部依赖，
    目标机无需安装运行库）；
  - 无论成功或失败都会驻留窗口几秒，避免双击后报错一闪而过看不到。
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
SPEC = os.path.join(ROOT, "WindowsLoopSend.spec")
FIXED_PY = r"C:\Users\jinn\AppData\Local\Programs\Python\Python312\python.exe"


class _Chk:
    """向 stdout 输出但立即刷新，确保管道/重定向时也能及时看到。"""

    @staticmethod
    def write(msg):
        print(msg)
        try:
            sys.stdout.flush()
        except Exception:
            pass


def detect_interpreter():
    """返回第一个装有 PyInstaller 的候选解释器绝对路径，否则返回 None。"""
    candidates = []
    if os.path.exists(FIXED_PY):
        candidates.append(FIXED_PY)
    if sys.executable and sys.executable.lower() not in {c.lower() for c in candidates}:
        candidates.append(sys.executable)

    for py in candidates:
        try:
            rc = subprocess.call([py, "-c", "import PyInstaller; print(PyInstaller.__version__)"],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if rc == 0:
                return py
        except Exception:
            continue
    return None


def main():
    py = detect_interpreter()
    if not py:
        _Chk.write("[ERROR] 未找到可用的 PyInstaller。")
        _Chk.write("  请先安装：")
        _Chk.write("      %s -m pip install pyinstaller" % FIXED_PY)
        sys.exit(1)

    _Chk.write("[1/2] 使用解释器: %s" % py)
    _Chk.write("[2/2] 清理旧构建并编译...")

    cmd = [py, "-m", "PyInstaller", "--clean", "--noconfirm", SPEC]
    # 强制以项目目录为工作目录（避免从 C:\Windows\system32 等任意位置运行被 PyInstaller 拒绝）
    rc = subprocess.call(cmd, cwd=ROOT)

    out = os.path.join(ROOT, "dist", "WindowsLoopSend.exe")
    if rc != 0:
        _Chk.write("")
        _Chk.write("[ERROR] 打包失败（PyInstaller 退出码=%s）。" % rc)
        _Chk.write("  完整日志见上方输出，或查看 build\\WindowsLoopSend\\warn-*.txt。")
        try:
            input("按回车退出...")
        except Exception:
            pass
        return 1

    _Chk.write("")
    _Chk.write("[OK] 打包完成：%s" % out)
    _Chk.write("      单文件，已内嵌 Python 与全部依赖，目标机无需安装运行库。")
    try:
        input("按回车退出...")
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())