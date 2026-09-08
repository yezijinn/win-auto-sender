import sys
import time
import datetime
import ctypes
from ctypes import wintypes
import pyperclip
import win32gui
import win32process
import win32con
import win32api

# 1. 解决高分屏/DPI缩放导致的点击错位 (必须在创建 QApplication 前调用)
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QTextEdit, QComboBox, QRadioButton, QButtonGroup,
    QDateEdit, QTimeEdit, QPushButton, QGroupBox, QGridLayout,
    QStatusBar, QSpinBox, QMessageBox, QCheckBox
)
from PySide6.QtCore import Qt, QTime, QDate, QThread, Signal
from PySide6.QtGui import QFont, QCursor, QPainter, QPen, QColor, QKeyEvent

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32


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
    if current_tid != target_tid:
        attached = bool(user32.AttachThreadInput(current_tid, target_tid, True))

    try:
        win32api.keybd_event(win32con.VK_MENU, 0, 0, 0)
        win32api.keybd_event(win32con.VK_MENU, 0, win32con.KEYEVENTF_KEYUP, 0)
        user32.SetForegroundWindow(hwnd)
        user32.BringWindowToTop(hwnd)
        user32.SetActiveWindow(hwnd)
    finally:
        if attached:
            user32.AttachThreadInput(current_tid, target_tid, False)

    time.sleep(0.15)
    return True


def click_and_paste_send(hwnd, text, click_pos=None):
    """点击 -> 剪贴板粘贴 -> 回车"""
    try:
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


class TargetPickerLabel(QLabel):
    targetCaptured = Signal(int, int, int, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(50, 50)
        self.setStyleSheet("border: 2px dashed #007acc; background-color: #f0f8ff; border-radius: 6px;")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.dragging = False

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor("#007acc"), 2)
        painter.setPen(pen)
        cx, cy = self.width() // 2, self.height() // 2
        r = 12
        painter.drawEllipse(cx - r, cy - r, r * 2, r * 2)
        painter.drawLine(cx - r - 5, cy, cx + r + 5, cy)
        painter.drawLine(cx, cy - r - 5, cx, cy + r + 5)

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
        # 按 ESC 取消瞄准拖拽，防止卡死
        if event.key() == Qt.Key.Key_Escape and self.dragging:
            self._finish_drag()

    def focusOutEvent(self, event):
        if self.dragging:
            self._finish_drag()

    def _finish_drag(self):
        self.dragging = False
        self.releaseMouse()
        self.setCursor(Qt.CursorShape.PointingHandCursor)


class SchedulerWorker(QThread):
    log_signal = Signal(str)
    task_done_signal = Signal()

    def __init__(self, config):
        super().__init__()
        self.config = config

    def run(self):
        hwnd = self.config['hwnd']
        prompt = self.config['prompt']
        mode = self.config['mode']
        start_date = self.config['start_date']
        end_date = self.config['end_date']
        trigger_time = self.config['trigger_time']
        interval_sec = self.config['interval_min'] * 60
        click_pos = self.config['click_pos']

        has_triggered = False
        last_loop_time = time.time() if not self.config.get('run_immediately', False) else 0

        self.log_signal.emit("监听调度引擎已就绪...")

        while not self.isInterruptionRequested():
            now = datetime.datetime.now()
            today = now.date()

            if not (start_date <= today <= end_date):
                time.sleep(0.5)
                continue

            if mode == 'single':
                target_dt = datetime.datetime.combine(today, trigger_time)
                if now >= target_dt and not has_triggered:
                    self.log_signal.emit("定时到达，正在聚焦并发送...")
                    ok = click_and_paste_send(hwnd, prompt, click_pos)
                    has_triggered = True
                    self.log_signal.emit("单次任务执行成功。" if ok else "单次任务执行失败。")
                    self.task_done_signal.emit()
                    break

            elif mode == 'loop':
                current_epoch = time.time()
                if current_epoch - last_loop_time >= interval_sec:
                    self.log_signal.emit("周期触发，正在发送提示词...")
                    click_and_paste_send(hwnd, prompt, click_pos)
                    last_loop_time = current_epoch
                    self.log_signal.emit(f"已执行，等待下个周期 ({self.config['interval_min']} 分钟后)...")

            for _ in range(5):
                if self.isInterruptionRequested():
                    break
                time.sleep(0.1)

        self.log_signal.emit("任务调度已完全停止。")


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        title_suffix = " (管理员)" if is_admin() else ""
        self.setWindowTitle(f"定时自动发送工具  -  作者 https://github.com/yezijinn{title_suffix}")
        self.setMinimumSize(580, 700)

        self.worker = None
        self.init_ui()
        self.refresh_window_list()

        if not is_admin():
            self.status_bar.showMessage("提示: 未使用管理员权限运行，若目标窗口具有高权限可能无法键入", 8000)

    def init_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setSpacing(10)

        # 1. 窗口绑定
        win_group = QGroupBox("1. 目标窗口绑定")
        win_layout = QHBoxLayout(win_group)
        self.win_combo = QComboBox()
        self.win_combo.setMinimumWidth(320)
        btn_refresh = QPushButton("刷新列表")
        btn_refresh.clicked.connect(self.refresh_window_list)

        self.picker_label = TargetPickerLabel()
        self.picker_label.targetCaptured.connect(self.on_target_captured)

        picker_box = QVBoxLayout()
        picker_box.addWidget(self.picker_label)
        picker_hint = QLabel("按住拖向目标")
        picker_hint.setStyleSheet("font-size: 10px; color: gray;")
        picker_box.addWidget(picker_hint)

        win_layout.addWidget(QLabel("目标窗口:"))
        win_layout.addWidget(self.win_combo, 1)
        win_layout.addWidget(btn_refresh)
        win_layout.addLayout(picker_box)
        layout.addWidget(win_group)

        # 2. 坐标
        coord_group = QGroupBox("2. 自动点击坐标（防焦点丢失）")
        coord_layout = QHBoxLayout(coord_group)
        self.spin_x = QSpinBox()
        self.spin_x.setRange(0, 9999)
        self.spin_x.setPrefix("屏幕 X: ")
        self.spin_y = QSpinBox()
        self.spin_y.setRange(0, 9999)
        self.spin_y.setPrefix("屏幕 Y: ")

        btn_clear_coord = QPushButton("清空坐标")
        btn_clear_coord.clicked.connect(lambda: (self.spin_x.setValue(0), self.spin_y.setValue(0)))

        coord_layout.addWidget(self.spin_x)
        coord_layout.addWidget(self.spin_y)
        coord_layout.addWidget(btn_clear_coord)
        coord_layout.addWidget(QLabel("（拖动上方准星到目标输入框松开即可）"))
        coord_layout.addStretch()
        layout.addWidget(coord_group)

# 3. 提示词
        prompt_group = QGroupBox("3. 预设提示词文本")
        p_layout = QVBoxLayout(prompt_group)
        self.txt_prompt = QTextEdit()
        
        # 默认新手指南（替换原有 setPlaceholderText 行）
        default_guide = (
            "请清空此处的指南文本，在此处输入你要发送的内容...\n"
            "1. 权限说明：建议右键本软件“以管理员身份运行”，避免因 Windows 权限隔离导致无法向 CMD、终端或某些 IDE 写入。\n"
            "2. 绑定窗口与焦点：\n"
            "   - 方法 A（推荐）：按住右上角的【准星图标】不放，拖拽到目标窗口的输入框中央，松开鼠标即可自动绑定窗口并记录点击坐标。\n"
            "   - 方法 B：在下拉框中手动挑选目标窗口，或直接使用坐标清空模式。\n"
            "3. 策略设置：\n"
            "   - 单次任务：在到达指定的【触发时间】时自动发送一次并停止。\n"
            "   - 循环任务：按设定的【循环间隔(分钟)】持续定时发送。\n"
            "4. 运行与验证：\n"
            "   - 可先点【立即测试触发 1 次】检验目标窗口是否能正常激活、粘贴并发送。\n"
            "   - 确认无误后点击【启动定时监听】挂机即可。\n"

        )
        self.txt_prompt.setPlainText(default_guide)
        
        p_layout.addWidget(self.txt_prompt)
        layout.addWidget(prompt_group)

        # 4. 触发策略
        time_group = QGroupBox("4. 时间与触发策略")
        t_layout = QGridLayout(time_group)

        self.rb_single = QRadioButton("单次任务")
        self.rb_loop = QRadioButton("循环任务")
        self.rb_single.setChecked(True)
        bg = QButtonGroup(self)
        bg.addButton(self.rb_single)
        bg.addButton(self.rb_loop)

        t_layout.addWidget(QLabel("任务模式:"), 0, 0)
        t_layout.addWidget(self.rb_single, 0, 1)
        t_layout.addWidget(self.rb_loop, 0, 2)

        t_layout.addWidget(QLabel("触发时间 (单次):"), 1, 0)
        self.time_picker = QTimeEdit()
        self.time_picker.setDisplayFormat("HH:mm:ss")
        self.time_picker.setTime(QTime.currentTime().addSecs(60))
        t_layout.addWidget(self.time_picker, 1, 1)

        t_layout.addWidget(QLabel("循环间隔 (分钟):"), 1, 2)
        self.spin_interval = QSpinBox()
        self.spin_interval.setRange(1, 1440)
        self.spin_interval.setValue(10)
        self.spin_interval.setEnabled(False)
        t_layout.addWidget(self.spin_interval, 1, 3)

        self.chk_immediate = QCheckBox("循环启动时立即执行第 1 次")
        self.chk_immediate.setEnabled(False)
        t_layout.addWidget(self.chk_immediate, 2, 2, 1, 2)

        self.rb_single.toggled.connect(self.toggle_mode_ui)

        t_layout.addWidget(QLabel("有效开始日期:"), 3, 0)
        self.date_start = QDateEdit(QDate.currentDate())
        self.date_start.setCalendarPopup(True)
        t_layout.addWidget(self.date_start, 3, 1)

        t_layout.addWidget(QLabel("有效截止日期:"), 3, 2)
        self.date_end = QDateEdit(QDate.currentDate().addDays(7))
        self.date_end.setCalendarPopup(True)
        t_layout.addWidget(self.date_end, 3, 3)
        layout.addWidget(time_group)

        # 5. 控制按钮
        btn_layout = QHBoxLayout()
        self.btn_toggle = QPushButton("▶ 启动定时监听")
        self.btn_toggle.setFixedHeight(42)
        self.btn_toggle.setStyleSheet("font-size: 14px; font-weight: bold;")
        self.btn_toggle.clicked.connect(self.toggle_task)

        btn_test = QPushButton("⚡ 立即测试触发 1 次")
        btn_test.setFixedHeight(42)
        btn_test.clicked.connect(self.test_trigger)

        btn_layout.addWidget(self.btn_toggle, 2)
        btn_layout.addWidget(btn_test, 1)
        layout.addLayout(btn_layout)

        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.showMessage("就绪")

    def toggle_mode_ui(self, is_single):
        self.spin_interval.setEnabled(not is_single)
        self.chk_immediate.setEnabled(not is_single)

    def refresh_window_list(self):
        self.win_combo.clear()
        windows = []

        def enum_cb(hwnd, extra):
            if win32gui.IsWindowVisible(hwnd):
                txt = win32gui.GetWindowText(hwnd)
                if txt.strip():
                    windows.append((txt, hwnd))
            return True

        win32gui.EnumWindows(enum_cb, None)
        windows.sort(key=lambda x: x[0].lower())

        for title, hwnd in windows:
            self.win_combo.addItem(f"{title} (HWND:{hwnd})", hwnd)

    def on_target_captured(self, hwnd, x, y, title):
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
            self.win_combo.insertItem(0, f"[捕获] {title} (HWND:{hwnd})", hwnd)
            self.win_combo.setCurrentIndex(0)

        self.status_bar.showMessage(f"已锁定坐标: ({x}, {y})，窗口: {title}")

    def collect_config(self):
        hwnd = self.win_combo.currentData()
        if not hwnd or not win32gui.IsWindow(hwnd):
            QMessageBox.warning(self, "警告", "请先选择一个有效的目标窗口！")
            return None

        prompt = self.txt_prompt.toPlainText()
        if not prompt.strip():
            QMessageBox.warning(self, "警告", "提示词内容不能为空！")
            return None

        start_d = self.date_start.date().toPython()
        end_d = self.date_end.date().toPython()
        if start_d > end_d:
            QMessageBox.warning(self, "警告", "开始日期不能晚于截止日期！")
            return None

        qtime = self.time_picker.time()
        py_time = datetime.time(qtime.hour(), qtime.minute(), qtime.second())

        if self.rb_single.isChecked():
            now = datetime.datetime.now()
            target_dt = datetime.datetime.combine(start_d, py_time)
            if target_dt <= now and start_d == now.date():
                reply = QMessageBox.question(
                    self, "时间提示", 
                    "所设单次时间已过去，是否自动顺延至明天？",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
                )
                if reply == QMessageBox.StandardButton.Yes:
                    self.date_start.setDate(QDate.currentDate().addDays(1))
                    start_d = self.date_start.date().toPython()
                else:
                    return None

        return {
            'hwnd': hwnd,
            'prompt': prompt,
            'click_pos': (self.spin_x.value(), self.spin_y.value()),
            'mode': 'single' if self.rb_single.isChecked() else 'loop',
            'trigger_time': py_time,
            'interval_min': self.spin_interval.value(),
            'run_immediately': self.chk_immediate.isChecked(),
            'start_date': start_d,
            'end_date': end_d
        }

    def toggle_task(self):
        if self.worker and self.worker.isRunning():
            self.worker.requestInterruption()
            while not self.worker.wait(100):
                QApplication.processEvents()
            self.worker = None
            self.btn_toggle.setText("▶ 启动定时监听")
            self.btn_toggle.setStyleSheet("")
            self.status_bar.showMessage("任务已安全停止。")
            return

        cfg = self.collect_config()
        if not cfg:
            return

        self.worker = SchedulerWorker(cfg)
        self.worker.log_signal.connect(self.status_bar.showMessage)
        self.worker.task_done_signal.connect(self.on_task_finished)
        self.worker.start()

        self.btn_toggle.setText("■ 停止监听")
        self.btn_toggle.setStyleSheet("background-color: #d9534f; color: white;")

    def on_task_finished(self):
        self.btn_toggle.setText("▶ 启动定时监听")
        self.btn_toggle.setStyleSheet("")

    def test_trigger(self):
        cfg = self.collect_config()
        if not cfg:
            return
        self.status_bar.showMessage("正在执行测试...")
        ok = click_and_paste_send(cfg['hwnd'], cfg['prompt'], cfg['click_pos'])
        if ok:
            self.status_bar.showMessage("测试完成：已聚焦并发送。")
        else:
            self.status_bar.showMessage("测试失败：请确认目标窗口权限是否高于本程序。")

    def closeEvent(self, event):
        if self.worker and self.worker.isRunning():
            self.worker.requestInterruption()
            while not self.worker.wait(100):
                QApplication.processEvents()
        event.accept()


if __name__ == '__main__':
    app = QApplication(sys.argv)
    app.setStyle('Fusion')
    font = QFont("Microsoft YaHei", 9)
    app.setFont(font)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())