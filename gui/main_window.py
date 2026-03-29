"""
Smart Search Controller - 메인 GUI
PyQt6 기반 관제 화면

사용법:
    python -m gui.main_window
"""

import sys
import json
import os
import time as _time
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
from PyQt6.QtGui import QImage, QPixmap, QAction, QFont, QPainter, QPen, QColor

import numpy as np

# 상위 디렉토리 import
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from controller import RemoteController
from target_finder import TargetFinder, SmartClicker
from tracking import FastUnitTracker
from gui.tracking_dialog import TrackingSetupDialog


class ScreenWidget(QLabel):
    """Agent 화면 표시 위젯 + 수동 조작 모드"""

    clicked = pyqtSignal(int, int, str, list)  # x, y, button, modifiers
    manual_mouse_pos = pyqtSignal(int, int)  # 절대 이미지 좌표 (x, y)
    manual_key_pressed = pyqtSignal(str)
    manual_key_released = pyqtSignal(str)
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

        # 추적 오버레이
        self._tracker: Optional['UnitTracker'] = None
        self._last_matches = []

        # 실시간 마우스 추적 (절대 좌표 → 60Hz 전송)
        self._pending_pos = None
        self._mouse_timer = QTimer()
        self._mouse_timer.timeout.connect(self._flush_mouse_pos)
        self._mouse_timer.setInterval(16)  # ~60Hz

        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    @property
    def manual_mode(self) -> bool:
        return self._manual_mode

    manual_release_all = pyqtSignal()  # 수동 조작 종료 시 모든 키 해제

    def set_manual_mode(self, enabled: bool):
        if self._manual_mode == enabled:
            return
        self._manual_mode = enabled
        if enabled:
            self.grabKeyboard()
            self.setCursor(Qt.CursorShape.CrossCursor)
            self.setStyleSheet("background-color: #2d2d2d; border: 2px solid #ff4a4a;")
            self._pending_pos = None
            self._mouse_timer.start()
        else:
            self._mouse_timer.stop()
            self._flush_mouse_pos()
            self.manual_release_all.emit()  # 모든 키/마우스 해제
            self.releaseKeyboard()
            self.unsetCursor()
            self.setStyleSheet("background-color: #2d2d2d; border: 1px solid #555;")
        self.manual_mode_changed.emit(enabled)

    def set_tracker(self, tracker: Optional['UnitTracker'], exclude_rect=None):
        """추적기 설정/해제"""
        self._tracker = tracker
        self._exclude_rect = exclude_rect
        if not tracker:
            self._last_matches = []

    def update_frame(self, frame: np.ndarray):
        """프레임 업데이트 + 추적 오버레이"""
        if frame is None:
            return

        h, w, ch = frame.shape
        self.original_width = w
        self.original_height = h
        bytes_per_line = ch * w

        # 추적 매칭 (추적기가 설정된 경우)
        if self._tracker and self._tracker.has_target():
            try:
                # RGB→BGR 변환 (OpenCV용)
                bgr = frame[:, :, ::-1].copy() if ch == 3 else frame
                self._last_matches = self._tracker.find_matches(bgr)
            except Exception:
                self._last_matches = []

        # RGB → QImage → QPixmap
        img = QImage(frame.data, w, h, bytes_per_line, QImage.Format.Format_RGB888)
        pixmap = QPixmap.fromImage(img)

        # 추적 박스 오버레이 그리기
        if self._last_matches or (self._tracker and self._tracker.has_target()):
            painter = QPainter(pixmap)
            cx, cy = w // 2, h // 2

            # 사용자 지정 제외 영역 표시
            exclude = self._exclude_rect if hasattr(self, '_exclude_rect') else None
            if exclude:
                ex, ey, ew, eh = exclude
                painter.setPen(QPen(QColor(100, 100, 255, 80), 1, Qt.PenStyle.DashLine))
                painter.setBrush(QColor(100, 100, 255, 30))
                painter.drawRect(ex, ey, ew, eh)
                painter.setBrush(Qt.BrushStyle.NoBrush)

            # 제외 영역 외 가장 가까운 유닛 찾기
            nearest_idx = -1
            nearest_dist = float('inf')
            for i, m in enumerate(self._last_matches):
                mx = m.x + m.w // 2
                my = m.y + m.h // 2
                if exclude:
                    ex, ey, ew, eh = exclude
                    if ex <= mx <= ex + ew and ey <= my <= ey + eh:
                        continue
                dist = ((mx - cx) ** 2 + (my - cy) ** 2) ** 0.5
                if dist < nearest_dist:
                    nearest_dist = dist
                    nearest_idx = i

            for i, m in enumerate(self._last_matches):
                mx = m.x + m.w // 2
                my = m.y + m.h // 2
                in_exclude = False
                if exclude:
                    ex, ey, ew, eh = exclude
                    in_exclude = ex <= mx <= ex + ew and ey <= my <= ey + eh

                if in_exclude:
                    pen = QPen(QColor(128, 128, 128, 100), 1)
                elif i == nearest_idx:
                    pen = QPen(QColor(255, 50, 50), 3)
                else:
                    pen = QPen(QColor(50, 255, 50), 2)
                painter.setPen(pen)
                painter.drawRect(m.x, m.y, m.w, m.h)

                # score 텍스트
                painter.setPen(QColor(255, 255, 255))
                painter.drawText(m.x, m.y - 3, f"{m.score:.2f}")

            painter.end()

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

    def mouseMoveEvent(self, event):
        """수동 조작 중 마우스 절대 좌표 추적"""
        if not self._manual_mode:
            return
        coords = self._map_to_original(event)
        if coords:
            self._pending_pos = coords

    def _flush_mouse_pos(self):
        """마지막 마우스 위치를 전송 (60Hz 타이머)"""
        if self._pending_pos:
            self.manual_mouse_pos.emit(*self._pending_pos)
            self._pending_pos = None

    def _get_modifiers(self, event) -> list:
        """현재 눌린 수정자 키 목록 반환"""
        mods = event.modifiers()
        result = []
        if mods & Qt.KeyboardModifier.ControlModifier:
            result.append("KEY_LEFT_CTRL")
        if mods & Qt.KeyboardModifier.ShiftModifier:
            result.append("KEY_LEFT_SHIFT")
        if mods & Qt.KeyboardModifier.AltModifier:
            result.append("KEY_LEFT_ALT")
        return result

    def mousePressEvent(self, event):
        """마우스 클릭 → 수동 조작 모드일 때만 동작 (수정자 키 포함)"""
        if not self._manual_mode:
            return
        coords = self._map_to_original(event)
        if not coords:
            return
        modifiers = self._get_modifiers(event)
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(*coords, "LEFT", modifiers)
        elif event.button() == Qt.MouseButton.RightButton:
            self.clicked.emit(*coords, "RIGHT", modifiers)
        elif event.button() == Qt.MouseButton.MiddleButton:
            self.clicked.emit(*coords, "MIDDLE", modifiers)

    def event(self, event):
        """수동 조작 중 Tab 키가 포커스 전환에 소비되지 않도록 가로채기"""
        if (self._manual_mode and event.type() in (
                event.Type.KeyPress, event.Type.KeyRelease)
                and event.key() == Qt.Key.Key_Tab):
            if event.type() == event.Type.KeyPress:
                self.keyPressEvent(event)
            else:
                self.keyReleaseEvent(event)
            return True
        return super().event(event)

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
    """Agent 패널 (4분할 고정 + 상태 + 컨트롤)"""

    SLOT_COUNT = 4  # 고정 4분할

    def __init__(self, name: str, controller: RemoteController):
        super().__init__(name)
        self.name = name
        self.ctrl = controller
        self.screen_widgets: Dict[str, ScreenWidget] = {}
        self.manual_buttons: Dict[str, QPushButton] = {}
        self.slot_containers: list = []
        self.active_window_id: str = "screen"

        self.setup_ui()

    def setup_ui(self):
        layout = QVBoxLayout()

        # 4분할 그리드 (2x2)
        self.grid_layout = QGridLayout()
        for i in range(self.SLOT_COUNT):
            container = QVBoxLayout()
            # 빈 슬롯: "추가 연결" 버튼
            placeholder = QPushButton(f"+ Gersang 추가 연결")
            placeholder.setMinimumSize(320, 240)
            placeholder.setStyleSheet(
                "background-color: #1a1a1a; border: 1px dashed #555; "
                "color: #888; font-size: 14px;")
            placeholder.clicked.connect(lambda _, idx=i: self._request_add_gersang(idx))

            container_widget = QWidget()
            container.addWidget(placeholder)
            container.setContentsMargins(0, 0, 0, 0)
            container_widget.setLayout(container)

            row, col = i // 2, i % 2
            self.grid_layout.addWidget(container_widget, row, col)
            self.slot_containers.append(container_widget)

        layout.addLayout(self.grid_layout)

        # 상태 표시
        status_layout = QHBoxLayout()
        self.status_label = QLabel("상태: 연결됨")
        self.fps_label = QLabel("FPS: -")
        self.window_label = QLabel("창: -")
        status_layout.addWidget(self.status_label)
        status_layout.addWidget(self.fps_label)
        status_layout.addWidget(self.window_label)
        layout.addLayout(status_layout)

        self.setLayout(layout)

    def _request_add_gersang(self, slot_idx: int):
        """빈 슬롯 클릭 → Agent에 새 Gersang 검색 요청"""
        print(f"[{self.name}] 슬롯 {slot_idx}: Gersang 추가 요청...")
        threading.Thread(
            target=self._do_add_gersang, args=(slot_idx,), daemon=True
        ).start()

    def _do_add_gersang(self, slot_idx: int):
        """Agent에 find_next_gersang 요청 (별도 스레드)"""
        result = self.ctrl.find_next_gersang(self.name)
        if result.get("success"):
            print(f"[{self.name}] 슬롯 {slot_idx}: {result.get('title')} 추가됨 ({result.get('window_id')})")
        else:
            print(f"[{self.name}] 슬롯 {slot_idx}: 추가 실패 - {result.get('error', '?')}")

    def ensure_screen_widget(self, window_id: str, title: str = "") -> ScreenWidget:
        """창 위젯 생성 → 해당 슬롯의 placeholder를 교체"""
        if window_id not in self.screen_widgets:
            # win0 → 슬롯 0, win1 → 슬롯 1, ...
            slot_idx = int(window_id.replace("win", "")) if window_id.startswith("win") else 0
            if slot_idx >= self.SLOT_COUNT:
                return None

            # 새 위젯 구성
            screen = ScreenWidget(f"{self.name}:{window_id}")
            screen.clicked.connect(
                lambda x, y, btn, mods, wid=window_id: self.on_screen_click(wid, x, y, btn, mods))
            screen.manual_mouse_pos.connect(
                lambda x, y: self.ctrl.send_realtime_mouse_pos(self.name, x, y))
            screen.manual_key_pressed.connect(
                lambda key: threading.Thread(
                    target=self.ctrl.send_command, args=(self.name, "key_down", {"key": key}),
                    kwargs={"human_like": False}, daemon=True).start())
            screen.manual_key_released.connect(
                lambda key: threading.Thread(
                    target=self.ctrl.send_command, args=(self.name, "key_up", {"key": key}),
                    kwargs={"human_like": False}, daemon=True).start())
            screen.manual_release_all.connect(
                lambda: threading.Thread(
                    target=self.ctrl.send_command, args=(self.name, "release_all", {}),
                    kwargs={"human_like": False}, daemon=True).start())

            btn_manual = QPushButton(f"수동 조작 [{title or window_id}]")
            btn_manual.setCheckable(True)
            btn_manual.setStyleSheet("QPushButton:checked { background-color: #cc3333; color: white; }")
            btn_manual.toggled.connect(lambda checked, s=screen: s.set_manual_mode(checked))
            screen.manual_mode_changed.connect(lambda on, b=btn_manual: b.setChecked(on))

            btn_track = QPushButton(f"추적 셋팅")
            btn_track.setStyleSheet("padding: 4px;")
            btn_track.clicked.connect(lambda _, wid=window_id: self._open_tracking_setup(wid))

            btn_track_toggle = QPushButton("▶")
            btn_track_toggle.setFixedWidth(32)
            btn_track_toggle.setCheckable(True)
            btn_track_toggle.setEnabled(False)
            btn_track_toggle.setStyleSheet("padding: 4px;")
            btn_track_toggle.toggled.connect(lambda on, wid=window_id, b=btn_track_toggle:
                self._toggle_tracking_active(wid, on, b))
            self._track_toggle_buttons = getattr(self, '_track_toggle_buttons', {})
            self._track_toggle_buttons[window_id] = btn_track_toggle

            btn_row = QHBoxLayout()
            btn_row.addWidget(btn_manual)
            btn_row.addWidget(btn_track)
            btn_row.addWidget(btn_track_toggle)

            container = QVBoxLayout()
            container.addWidget(screen)
            container.addLayout(btn_row)
            container.setContentsMargins(0, 0, 0, 0)

            new_widget = QWidget()
            new_widget.setLayout(container)

            # 기존 슬롯 위젯 교체
            row, col = slot_idx // 2, slot_idx % 2
            old_widget = self.slot_containers[slot_idx]
            self.grid_layout.replaceWidget(old_widget, new_widget)
            old_widget.deleteLater()
            self.slot_containers[slot_idx] = new_widget

            self.screen_widgets[window_id] = screen
            self.manual_buttons[window_id] = btn_manual

        return self.screen_widgets[window_id]

    def _toggle_tracking_active(self, window_id: str, on: bool, btn: QPushButton):
        """추적 ON/OFF 토글 (프리셋 유지)"""
        if not hasattr(self, '_trackers') or window_id not in self._trackers:
            btn.setChecked(False)
            return

        if on:
            self._tracking_active[window_id] = True
            exclude = getattr(self, '_exclude_rects', {}).get(window_id)
            if window_id in self.screen_widgets:
                self.screen_widgets[window_id].set_tracker(self._trackers[window_id], exclude)
            btn.setText("⏸")
            btn.setStyleSheet("background-color: #cc8800; color: white; padding: 4px;")
            # 우클릭 루프 재시작
            threading.Thread(
                target=self._tracking_loop, args=(window_id,),
                daemon=True
            ).start()
            print(f"[{self.name}:{window_id}] 추적 재개")
        else:
            self._tracking_active[window_id] = False
            if window_id in self.screen_widgets:
                self.screen_widgets[window_id].set_tracker(None)
            btn.setText("▶")
            btn.setStyleSheet("padding: 4px;")
            print(f"[{self.name}:{window_id}] 추적 일시정지 (프리셋 유지)")

    def _open_tracking_setup(self, window_id: str):
        """추적 셋팅 — 기존 프리셋 유지, 추가/삭제 가능"""
        windows = self.ctrl.get_windows(self.name)
        if window_id not in windows or windows[window_id].frame is None:
            print(f"[{self.name}:{window_id}] 프레임 없음")
            return

        frame = windows[window_id].frame.copy()

        if not hasattr(self, '_trackers'):
            self._trackers = {}
            self._tracking_active = {}

        # 기존 프리셋 크롭 이미지 + 제외 영역 전달
        existing = self._trackers.get(window_id)
        existing_crops = existing.get_crop_images_rgb() if existing else []
        existing_exclude = getattr(self, '_exclude_rects', {}).get(window_id)

        dialog = TrackingSetupDialog(frame, existing_crops=existing_crops,
                                     exclude_rect=existing_exclude, parent=None)

        if dialog.exec() == TrackingSetupDialog.DialogCode.Accepted:
            result = dialog.get_result()
            if result:
                tracker = FastUnitTracker()
                tracker.match_threshold = result["threshold"]

                for crop_rgb in result["crop_images"]:
                    crop_bgr = crop_rgb[:, :, ::-1].copy()
                    tracker.add_preset_from_crop(crop_bgr)

                self._trackers[window_id] = tracker

                # 제외 영역 저장
                if not hasattr(self, '_exclude_rects'):
                    self._exclude_rects = {}
                self._exclude_rects[window_id] = result.get("exclude_rect")
                self._tracking_active[window_id] = True

                if window_id in self.screen_widgets:
                    self.screen_widgets[window_id].set_tracker(
                        tracker, self._exclude_rects.get(window_id))

                toggle_btn = self._track_toggle_buttons.get(window_id)
                if toggle_btn:
                    toggle_btn.setEnabled(True)
                    toggle_btn.setChecked(True)

                print(f"[{self.name}:{window_id}] 추적 — 프리셋 {len(tracker.presets)}개, 임계값 {result['threshold']:.2f}")

                # 자동 우클릭 루프 시작
                threading.Thread(
                    target=self._tracking_loop, args=(window_id,),
                    daemon=True
                ).start()

    def _tracking_loop(self, window_id: str):
        """추적 루프 — 매칭 + 가장 가까운 유닛으로 마우스 이동 (사용자 지정 제외 영역)"""
        click_cooldown = 2.0
        last_click = 0
        loop_count = 0

        print(f"[추적-DBG] 루프 시작: window_id={window_id}, agent={self.name}")

        while self._tracking_active.get(window_id, False):
            try:
                loop_count += 1
                tracker = self._trackers.get(window_id)
                if not tracker or not tracker.has_target():
                    print(f"[추적-DBG] 루프 종료: tracker={tracker is not None}, has_target={tracker.has_target() if tracker else 'N/A'}")
                    break

                windows = self.ctrl.get_windows(self.name)
                if window_id not in windows or windows[window_id].frame is None:
                    if loop_count <= 3:
                        print(f"[추적-DBG] 프레임 없음: window_id={window_id}, available={list(windows.keys())}")
                    _time.sleep(0.1)
                    continue

                frame = windows[window_id].frame
                fh, fw = frame.shape[:2]
                cx, cy = fw // 2, fh // 2

                # 사용자 지정 제외 영역
                exclude = getattr(self, '_exclude_rects', {}).get(window_id)

                bgr = frame[:, :, ::-1].copy() if frame.shape[2] == 3 else frame
                matches = tracker.find_matches(bgr)

                # 제외 영역 내 매칭 필터링
                filtered = []
                for m in matches:
                    mx = m.x + m.w // 2
                    my = m.y + m.h // 2
                    if exclude:
                        ex, ey, ew, eh = exclude
                        if ex <= mx <= ex + ew and ey <= my <= ey + eh:
                            continue
                    filtered.append(m)

                if loop_count <= 3:
                    print(f"[추적-DBG] 매칭: total={len(matches)}, filtered={len(filtered)}")

                if filtered:
                    nearest = min(filtered, key=lambda m:
                        ((m.x + m.w // 2 - cx) ** 2 + (m.y + m.h // 2 - cy) ** 2))
                    click_x = int(nearest.x + nearest.w // 2)
                    click_y = int(nearest.y + nearest.h // 2)

                    print(f"[추적:{window_id}] 이동+우클릭 ({click_x},{click_y}) score={nearest.score:.2f}")
                    # human-like 이동 + 우클릭 (agent에서 베지어 곡선 이동)
                    if self.name in self.ctrl.agents and self.ctrl._loop:
                        import asyncio
                        agent = self.ctrl.agents[self.name]
                        cmd = {"type": "move_and_click",
                               "params": {"x": click_x, "y": click_y, "button": "RIGHT"}}
                        asyncio.run_coroutine_threadsafe(
                            self.ctrl._send_fire_and_forget(agent, cmd), self.ctrl._loop)
                    last_click = _time.time()

                _time.sleep(0.1)  # 10Hz 매칭 주기

            except Exception as e:
                print(f"[추적:{window_id}] 오류: {e}")
                import traceback
                traceback.print_exc()
                _time.sleep(0.5)

        print(f"[추적-DBG] 루프 종료: loop_count={loop_count}, active={self._tracking_active.get(window_id, False)}")

    def stop_tracking(self, window_id: str):
        """추적 중지 (프리셋은 유지)"""
        if hasattr(self, '_tracking_active'):
            self._tracking_active[window_id] = False
        if window_id in self.screen_widgets:
            self.screen_widgets[window_id].set_tracker(None)
        toggle_btn = getattr(self, '_track_toggle_buttons', {}).get(window_id)
        if toggle_btn:
            toggle_btn.setChecked(False)
        print(f"[{self.name}:{window_id}] 추적 중지 (프리셋 유지)")

    def on_screen_click(self, window_id: str, x: int, y: int,
                        button: str = "LEFT", modifiers: list = None):
        """화면 클릭 → 별도 스레드에서 수정자키+이동+클릭"""
        mods = modifiers or []
        mod_label = "+".join(m.replace("KEY_LEFT_", "") for m in mods)
        btn_label = {"LEFT": "좌클릭", "RIGHT": "우클릭", "MIDDLE": "중클릭"}.get(button, button)
        if mod_label:
            print(f"[{self.name}:{window_id}] {mod_label}+{btn_label} ({x}, {y})")
        else:
            print(f"[{self.name}:{window_id}] {btn_label} ({x}, {y})")
        threading.Thread(
            target=self._do_click_with_modifiers,
            args=(window_id, x, y, button, mods),
            daemon=True
        ).start()

    def _do_click_with_modifiers(self, window_id: str, x: int, y: int,
                                  button: str, modifiers: list):
        """수정자 키 누르기 → 이동+클릭 → 수정자 키 떼기"""
        # 수정자 키 누르기
        for mod in modifiers:
            self.ctrl.send_command(self.name, "key_down", {"key": mod}, human_like=False)
        # 이동 + 클릭
        self.ctrl.send_click_to_window(self.name, window_id, x, y, button=button)
        # 수정자 키 떼기
        for mod in modifiers:
            self.ctrl.send_command(self.name, "key_up", {"key": mod}, human_like=False)

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
        if self.empty_label.isVisible():
            self.empty_label.hide()

        panel = AgentPanel(name, self.ctrl)
        self.agent_panels[name] = panel

        # 전체 영역에 추가 (AgentPanel이 내부 2x2 그리드 가짐)
        self.screen_layout.addWidget(panel, 0, 0)

        self.ctrl.on_frame(name, lambda n, w, f: None)

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
    # 화면 중앙 배치
    screen = app.primaryScreen().availableGeometry()
    window.resize(min(1400, screen.width()), min(900, screen.height()))
    window.move((screen.width() - window.width()) // 2,
                (screen.height() - window.height()) // 2)
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
