"""
Smart Search Controller - 메인 GUI
PyQt6 기반 관제 화면

사용법:
    python -m gui.main_window
"""

import sys
import json
import os
import threading
from typing import Optional, Dict

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QGridLayout, QLabel, QPushButton, QLineEdit, QTextEdit,
    QGroupBox, QScrollArea, QSplitter, QStatusBar, QMenuBar,
    QMenu, QDialog, QDialogButtonBox, QFormLayout, QSpinBox,
    QComboBox, QMessageBox, QFileDialog
)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QThread
from PyQt6.QtGui import QImage, QPixmap, QAction, QFont

import numpy as np

# 상위 디렉토리 import
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from controller import RemoteController
from target_finder import TargetFinder, SmartClicker


class ScreenWidget(QLabel):
    """Agent 화면 표시 위젯 + 수동 조작 모드"""

    clicked = pyqtSignal(int, int, str)  # x, y, button
    manual_key_pressed = pyqtSignal(str)  # HID key name
    manual_key_released = pyqtSignal(str)  # HID key name
    manual_mode_changed = pyqtSignal(bool)

    # Qt 키 → Leonardo HID 키 이름 매핑
    _QT_TO_HID = {
        Qt.Key.Key_Return: "KEY_RETURN", Qt.Key.Key_Enter: "KEY_RETURN",
        Qt.Key.Key_Escape: "KEY_ESC", Qt.Key.Key_Backspace: "KEY_BACKSPACE",
        Qt.Key.Key_Tab: "KEY_TAB", Qt.Key.Key_Space: "KEY_SPACE",
        Qt.Key.Key_Delete: "KEY_DELETE", Qt.Key.Key_Insert: "KEY_INSERT",
        Qt.Key.Key_Home: "KEY_HOME", Qt.Key.Key_End: "KEY_END",
        Qt.Key.Key_PageUp: "KEY_PAGE_UP", Qt.Key.Key_PageDown: "KEY_PAGE_DOWN",
        Qt.Key.Key_Up: "KEY_UP", Qt.Key.Key_Down: "KEY_DOWN",
        Qt.Key.Key_Left: "KEY_LEFT", Qt.Key.Key_Right: "KEY_RIGHT",
        Qt.Key.Key_CapsLock: "KEY_CAPS_LOCK",
        Qt.Key.Key_F1: "KEY_F1", Qt.Key.Key_F2: "KEY_F2",
        Qt.Key.Key_F3: "KEY_F3", Qt.Key.Key_F4: "KEY_F4",
        Qt.Key.Key_F5: "KEY_F5", Qt.Key.Key_F6: "KEY_F6",
        Qt.Key.Key_F7: "KEY_F7", Qt.Key.Key_F8: "KEY_F8",
        Qt.Key.Key_F9: "KEY_F9", Qt.Key.Key_F10: "KEY_F10",
        Qt.Key.Key_F11: "KEY_F11", Qt.Key.Key_F12: "KEY_F12",
        Qt.Key.Key_Control: "KEY_LEFT_CTRL", Qt.Key.Key_Shift: "KEY_LEFT_SHIFT",
        Qt.Key.Key_Alt: "KEY_LEFT_ALT", Qt.Key.Key_Meta: "KEY_LEFT_GUI",
    }

    def __init__(self, agent_name: str = ""):
        super().__init__()
        self.agent_name = agent_name
        self.setMinimumSize(320, 240)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setStyleSheet("background-color: #2d2d2d; border: 1px solid #555;")
        self.setText("연결 대기중...")

        self.original_width = 0
        self.original_height = 0
        self._manual_mode = False

        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    @property
    def manual_mode(self) -> bool:
        return self._manual_mode

    def set_manual_mode(self, enabled: bool):
        if self._manual_mode == enabled:
            return
        self._manual_mode = enabled
        if enabled:
            self.grabKeyboard()
            self.setCursor(Qt.CursorShape.CrossCursor)
            self.setStyleSheet("background-color: #2d2d2d; border: 2px solid #ff4a4a;")
        else:
            self.releaseKeyboard()
            self.unsetCursor()
            self.setStyleSheet("background-color: #2d2d2d; border: 1px solid #555;")
        self.manual_mode_changed.emit(enabled)

    def update_frame(self, frame: np.ndarray):
        """프레임 업데이트"""
        if frame is None:
            return

        h, w, ch = frame.shape
        self.original_width = w
        self.original_height = h
        bytes_per_line = ch * w

        # RGB → QImage
        img = QImage(frame.data, w, h, bytes_per_line, QImage.Format.Format_RGB888)
        pixmap = QPixmap.fromImage(img)

        # 위젯 크기에 맞게 스케일
        scaled = pixmap.scaled(self.size(), Qt.AspectRatioMode.KeepAspectRatio,
                               Qt.TransformationMode.SmoothTransformation)
        self.setPixmap(scaled)

    def _map_to_original(self, event) -> tuple:
        """위젯 좌표 → 원본 이미지 좌표 변환. 실패 시 None 반환"""
        pixmap = self.pixmap()
        if not pixmap or self.original_width <= 0:
            return None

        widget_w, widget_h = self.width(), self.height()
        pixmap_w, pixmap_h = pixmap.width(), pixmap.height()

        # 중앙 정렬 오프셋
        offset_x = (widget_w - pixmap_w) // 2
        offset_y = (widget_h - pixmap_h) // 2

        click_x = event.position().x() - offset_x
        click_y = event.position().y() - offset_y

        if 0 <= click_x < pixmap_w and 0 <= click_y < pixmap_h:
            scale_x = self.original_width / pixmap_w
            scale_y = self.original_height / pixmap_h
            return int(click_x * scale_x), int(click_y * scale_y)
        return None

    def mousePressEvent(self, event):
        """마우스 클릭 → 수동 조작 모드일 때만 동작"""
        if not self._manual_mode:
            return
        coords = self._map_to_original(event)
        if not coords:
            return
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(*coords, "LEFT")
        elif event.button() == Qt.MouseButton.RightButton:
            self.clicked.emit(*coords, "RIGHT")
        elif event.button() == Qt.MouseButton.MiddleButton:
            self.clicked.emit(*coords, "MIDDLE")

    def _qt_key_to_hid(self, event) -> Optional[str]:
        """Qt 키이벤트 → HID 키 이름"""
        key = event.key()
        if key in self._QT_TO_HID:
            return self._QT_TO_HID[key]
        text = event.text()
        if text and len(text) == 1 and 32 <= ord(text) <= 126:
            return text
        return None

    def keyPressEvent(self, event):
        if not self._manual_mode:
            super().keyPressEvent(event)
            return
        # Ctrl+Shift+Q: 수동 조작 종료
        mods = event.modifiers()
        if (event.key() == Qt.Key.Key_Q and
                mods & Qt.KeyboardModifier.ControlModifier and
                mods & Qt.KeyboardModifier.ShiftModifier):
            self.set_manual_mode(False)
            return
        if event.isAutoRepeat():
            return
        hid_key = self._qt_key_to_hid(event)
        if hid_key:
            self.manual_key_pressed.emit(hid_key)

    def keyReleaseEvent(self, event):
        if not self._manual_mode:
            super().keyReleaseEvent(event)
            return
        if event.isAutoRepeat():
            return
        hid_key = self._qt_key_to_hid(event)
        if hid_key:
            self.manual_key_released.emit(hid_key)


class AgentPanel(QGroupBox):
    """Agent 패널 (다중 창 + 상태 + 컨트롤)"""

    def __init__(self, name: str, controller: RemoteController):
        super().__init__(name)
        self.name = name
        self.ctrl = controller
        self.screen_widgets: Dict[str, ScreenWidget] = {}
        self.manual_buttons: Dict[str, QPushButton] = {}
        self.active_window_id: str = "screen"

        self.setup_ui()

    def setup_ui(self):
        layout = QVBoxLayout()

        # 다중 창 표시 영역 (가로 배치)
        self.screens_layout = QHBoxLayout()
        layout.addLayout(self.screens_layout)

        # 상태 표시
        status_layout = QHBoxLayout()
        self.status_label = QLabel("상태: 연결됨")
        self.fps_label = QLabel("FPS: -")
        self.window_label = QLabel("창: -")
        status_layout.addWidget(self.status_label)
        status_layout.addWidget(self.fps_label)
        status_layout.addWidget(self.window_label)
        layout.addLayout(status_layout)

        # 간단 컨트롤
        ctrl_layout = QHBoxLayout()
        self.btn_refresh = QPushButton("새로고침")
        ctrl_layout.addWidget(self.btn_refresh)
        layout.addLayout(ctrl_layout)

        self.setLayout(layout)

    def ensure_screen_widget(self, window_id: str, title: str = "") -> ScreenWidget:
        """창 위젯 생성 (수동 조작 버튼 포함)"""
        if window_id not in self.screen_widgets:
            container = QVBoxLayout()

            screen = ScreenWidget(f"{self.name}:{window_id}")
            screen.clicked.connect(
                lambda x, y, btn, wid=window_id: self.on_screen_click(wid, x, y, btn))
            screen.manual_key_pressed.connect(
                lambda key: threading.Thread(
                    target=self.ctrl.send_key, args=(self.name, key),
                    kwargs={"human_like": False}, daemon=True).start())
            screen.manual_key_released.connect(
                lambda key: threading.Thread(
                    target=self.ctrl.send_command, args=(self.name, "key_up", {"key": key}),
                    kwargs={"human_like": False}, daemon=True).start())

            btn = QPushButton(f"수동 조작 [{title or window_id}]")
            btn.setCheckable(True)
            btn.setStyleSheet("QPushButton:checked { background-color: #cc3333; color: white; }")
            btn.toggled.connect(lambda checked, s=screen: s.set_manual_mode(checked))
            screen.manual_mode_changed.connect(lambda on, b=btn: b.setChecked(on))

            container_widget = QWidget()
            container.addWidget(screen)
            container.addWidget(btn)
            container.setContentsMargins(0, 0, 0, 0)
            container_widget.setLayout(container)

            self.screen_widgets[window_id] = screen
            self.manual_buttons[window_id] = btn
            self.screens_layout.addWidget(container_widget)

        return self.screen_widgets[window_id]

    def on_screen_click(self, window_id: str, x: int, y: int, button: str = "LEFT"):
        """화면 클릭 → 별도 스레드에서 이동 + 클릭 (GUI 블로킹 방지)"""
        btn_label = {"LEFT": "좌클릭", "RIGHT": "우클릭", "MIDDLE": "중클릭"}.get(button, button)
        print(f"[{self.name}:{window_id}] {btn_label} ({x}, {y})")
        threading.Thread(
            target=self.ctrl.send_click_to_window,
            args=(self.name, window_id, x, y),
            kwargs={"button": button},
            daemon=True
        ).start()

    def update_frame(self, window_id: str, frame: np.ndarray, title: str = "", active: bool = False):
        """창별 프레임 업데이트"""
        screen = self.ensure_screen_widget(window_id, title)
        screen.update_frame(frame)

        # 활성 창 표시 (수동 조작 중이면 빨간 테두리 유지)
        if not screen.manual_mode:
            if active:
                self.active_window_id = window_id
                screen.setStyleSheet("background-color: #2d2d2d; border: 2px solid #4a9eff;")
            else:
                screen.setStyleSheet("background-color: #2d2d2d; border: 1px solid #555;")

        # 수동 조작 버튼 텍스트 업데이트
        if window_id in self.manual_buttons and title:
            btn = self.manual_buttons[window_id]
            if not btn.isChecked():
                btn.setText(f"수동 조작 [{title[:20]}]")

    def update_all_frames(self, windows: dict):
        """모든 창 프레임 업데이트"""
        for window_id, wf in windows.items():
            if wf.frame is not None:
                self.update_frame(window_id, wf.frame, wf.title, wf.active)

        self.window_label.setText(f"창: {len(windows)}개")

    def update_status(self, connected: bool, fps: int = 0, ping: float = 0):
        """상태 업데이트"""
        self.status_label.setText(f"상태: {'연결됨' if connected else '끊김'}")
        self.fps_label.setText(f"FPS: {fps}")


class ActionButton(QPushButton):
    """액션 버튼"""

    def __init__(self, action_config: dict, callback):
        super().__init__(action_config.get("name", "Action"))
        self.config = action_config
        self.callback = callback

        self.setMinimumSize(80, 40)
        self.clicked.connect(self.on_click)

        # 툴팁
        steps = action_config.get("steps", [])
        tooltip = "\n".join([f"- {s.get('action', '')}" for s in steps])
        self.setToolTip(tooltip)

    def on_click(self):
        self.callback(self.config)


class ConnectDialog(QDialog):
    """Agent 연결 다이얼로그"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Agent 연결")
        self.setup_ui()

    def setup_ui(self):
        layout = QFormLayout()

        self.name_edit = QLineEdit("PC1")
        self.host_edit = QLineEdit("192.168.123.111")
        self.port_spin = QSpinBox()
        self.port_spin.setRange(1, 65535)
        self.port_spin.setValue(8765)

        layout.addRow("이름:", self.name_edit)
        layout.addRow("호스트:", self.host_edit)
        layout.addRow("포트:", self.port_spin)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.setLayout(layout)

    def get_values(self):
        return {
            "name": self.name_edit.text(),
            "host": self.host_edit.text(),
            "port": self.port_spin.value()
        }


class MainWindow(QMainWindow):
    """메인 윈도우"""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Smart Search Controller")
        self.setMinimumSize(1200, 800)

        # 컨트롤러 초기화
        self.ctrl = RemoteController()
        self.finder = TargetFinder()
        self.clicker = SmartClicker(self.ctrl, self.finder)

        # Agent 패널들
        self.agent_panels: Dict[str, AgentPanel] = {}

        # 액션 설정
        self.actions = []
        self.load_actions()

        self.setup_ui()
        self.setup_menu()
        self.setup_timer()

    def setup_ui(self):
        """UI 구성"""
        central = QWidget()
        self.setCentralWidget(central)

        main_layout = QVBoxLayout(central)

        # 스플리터 (화면 영역 + 컨트롤 영역)
        splitter = QSplitter(Qt.Orientation.Vertical)

        # === 상단: Agent 화면들 ===
        screen_area = QWidget()
        self.screen_layout = QGridLayout(screen_area)
        self.screen_layout.setSpacing(10)

        # 빈 상태 라벨
        self.empty_label = QLabel("Agent를 연결하세요 (파일 → 연결)")
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_label.setStyleSheet("color: #888; font-size: 16px;")
        self.screen_layout.addWidget(self.empty_label, 0, 0)

        splitter.addWidget(screen_area)

        # === 하단: 액션 버튼 + 로그 ===
        bottom_area = QWidget()
        bottom_layout = QHBoxLayout(bottom_area)

        # 액션 버튼 그룹
        action_group = QGroupBox("액션")
        self.action_layout = QGridLayout(action_group)
        self.update_action_buttons()
        bottom_layout.addWidget(action_group, 1)

        # 로그 영역
        log_group = QGroupBox("로그")
        log_layout = QVBoxLayout(log_group)
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setMaximumHeight(150)
        log_layout.addWidget(self.log_text)
        bottom_layout.addWidget(log_group, 2)

        splitter.addWidget(bottom_area)
        splitter.setSizes([600, 200])

        main_layout.addWidget(splitter)

        # 상태바
        self.statusBar().showMessage("준비")

    def setup_menu(self):
        """메뉴 구성"""
        menubar = self.menuBar()

        # 파일 메뉴
        file_menu = menubar.addMenu("파일")

        connect_action = QAction("Agent 연결...", self)
        connect_action.triggered.connect(self.show_connect_dialog)
        file_menu.addAction(connect_action)

        disconnect_action = QAction("모두 연결 해제", self)
        disconnect_action.triggered.connect(self.disconnect_all)
        file_menu.addAction(disconnect_action)

        file_menu.addSeparator()

        exit_action = QAction("종료", self)
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

        # 설정 메뉴
        settings_menu = menubar.addMenu("설정")

        edit_actions = QAction("액션 편집...", self)
        edit_actions.triggered.connect(self.edit_actions)
        settings_menu.addAction(edit_actions)

        reload_actions = QAction("액션 새로고침", self)
        reload_actions.triggered.connect(self.reload_actions)
        settings_menu.addAction(reload_actions)

    def setup_timer(self):
        """업데이트 타이머"""
        self.update_timer = QTimer()
        self.update_timer.timeout.connect(self.update_frames)
        self.update_timer.start(66)  # ~15 FPS

    def show_connect_dialog(self):
        """연결 다이얼로그 표시"""
        dialog = ConnectDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            values = dialog.get_values()
            self.connect_agent(values["name"], values["host"], values["port"])

    def connect_agent(self, name: str, host: str, port: int):
        """Agent 연결"""
        self.log(f"연결 시도: {name} ({host}:{port})")

        if self.ctrl.connect(host, port, name):
            self.log(f"[OK] 연결됨: {name}")
            self.add_agent_panel(name)
            self.statusBar().showMessage(f"연결됨: {name}")
        else:
            self.log(f"[ERROR] 연결 실패: {name}")
            QMessageBox.warning(self, "연결 실패", f"{host}:{port}에 연결할 수 없습니다.")

    def add_agent_panel(self, name: str):
        """Agent 패널 추가"""
        # 빈 라벨 제거
        if self.empty_label.isVisible():
            self.empty_label.hide()

        panel = AgentPanel(name, self.ctrl)
        self.agent_panels[name] = panel

        # 그리드에 추가 (2열)
        count = len(self.agent_panels) - 1
        row = count // 2
        col = count % 2
        self.screen_layout.addWidget(panel, row, col)

        # 프레임 콜백 등록 (3개 인자: agent_name, window_id, frame)
        self.ctrl.on_frame(name, lambda n, w, f: None)  # 콜백은 타이머에서 처리

    def disconnect_all(self):
        """모든 연결 해제"""
        for name in list(self.agent_panels.keys()):
            self.screen_layout.removeWidget(self.agent_panels[name])
            self.agent_panels[name].deleteLater()
            del self.agent_panels[name]

        self.ctrl.disconnect_all()
        self.empty_label.show()
        self.log("모든 연결 해제됨")

    def update_frames(self):
        """프레임 업데이트 (타이머)"""
        for name, panel in self.agent_panels.items():
            # 다중 창 프레임 업데이트
            windows = self.ctrl.get_windows(name)
            if windows:
                panel.update_all_frames(windows)

            # 상태 업데이트
            connected = self.ctrl.is_connected(name)
            panel.update_status(connected)

    def load_actions(self):
        """액션 설정 로드"""
        actions_path = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                                    "actions", "macros.json")
        if os.path.exists(actions_path):
            try:
                with open(actions_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.actions = data.get("actions", [])
                    print(f"[OK] 액션 로드: {len(self.actions)}개")
            except Exception as e:
                print(f"[ERROR] 액션 로드 실패: {e}")
                self.actions = []
        else:
            self.actions = []

    def reload_actions(self):
        """액션 새로고침"""
        self.load_actions()
        self.update_action_buttons()
        self.log("액션 설정 새로고침됨")

    def update_action_buttons(self):
        """액션 버튼 업데이트"""
        # 기존 버튼 제거
        while self.action_layout.count():
            item = self.action_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        # 새 버튼 추가
        for i, action in enumerate(self.actions):
            btn = ActionButton(action, self.execute_action)
            row = i // 5
            col = i % 5
            self.action_layout.addWidget(btn, row, col)

        # 빈 공간 채우기
        if not self.actions:
            label = QLabel("액션이 없습니다.\n설정 → 액션 편집에서 추가하세요.")
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label.setStyleSheet("color: #888;")
            self.action_layout.addWidget(label, 0, 0)

    def execute_action(self, action_config: dict):
        """액션 실행"""
        action_name = action_config.get("name", "Unknown")
        steps = action_config.get("steps", [])

        # 첫 번째 연결된 Agent에서 실행 (TODO: Agent 선택 UI 추가)
        if not self.agent_panels:
            self.log("[ERROR] 연결된 Agent 없음")
            return

        agent_name = list(self.agent_panels.keys())[0]
        self.log(f"[{agent_name}] 액션 실행: {action_name}")

        for step in steps:
            action = step.get("action")
            self.execute_step(agent_name, step)

    def execute_step(self, agent_name: str, step: dict):
        """단일 스텝 실행"""
        action = step.get("action")
        human_like = step.get("human_like", True)

        try:
            if action == "find_and_click":
                template = step.get("template")
                self.clicker.click_template(agent_name, template, human_like=human_like)

            elif action == "wait":
                import time
                seconds = step.get("seconds", 1)
                time.sleep(seconds)

            elif action == "type_text":
                text = step.get("text", "")
                self.ctrl.send_type(agent_name, text, human_like)

            elif action == "key":
                key = step.get("key", "")
                self.ctrl.send_key(agent_name, key, human_like)

            elif action == "combo":
                keys = step.get("keys", [])
                self.ctrl.send_combo(agent_name, keys, human_like)

            elif action == "click":
                x, y = step.get("x", 0), step.get("y", 0)
                self.ctrl.send_click(agent_name, x, y, human_like=human_like)

            else:
                self.log(f"[WARN] 알 수 없는 액션: {action}")

        except Exception as e:
            self.log(f"[ERROR] 스텝 실행 실패: {e}")

    def edit_actions(self):
        """액션 편집 (외부 에디터로 열기)"""
        actions_path = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                                    "actions", "macros.json")

        # 파일 없으면 기본 템플릿 생성
        if not os.path.exists(actions_path):
            default = {
                "actions": [
                    {
                        "name": "예시 액션",
                        "steps": [
                            {"action": "find_and_click", "template": "button.png"},
                            {"action": "wait", "seconds": 1},
                            {"action": "type_text", "text": "Hello", "human_like": True}
                        ]
                    }
                ]
            }
            os.makedirs(os.path.dirname(actions_path), exist_ok=True)
            with open(actions_path, "w", encoding="utf-8") as f:
                json.dump(default, f, ensure_ascii=False, indent=2)

        # 기본 에디터로 열기
        import subprocess
        subprocess.Popen(["notepad", actions_path])
        self.log(f"액션 파일 열림: {actions_path}")

    def log(self, message: str):
        """로그 추가"""
        import datetime
        timestamp = datetime.datetime.now().strftime("%H:%M:%S")
        self.log_text.append(f"[{timestamp}] {message}")
        # 자동 스크롤
        scrollbar = self.log_text.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def closeEvent(self, event):
        """종료 시 정리"""
        self.update_timer.stop()
        self.ctrl.disconnect_all()
        event.accept()


def main():
    app = QApplication(sys.argv)

    # 다크 테마 (간단)
    app.setStyle("Fusion")

    window = MainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
