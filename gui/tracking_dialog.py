"""
추적 셋팅 다이얼로그 — 스크린샷 정지 + 8방향 프리셋 드래그 박싱
기존 프리셋 유지 + 개별 삭제 가능
"""

import numpy as np
from typing import Optional, Tuple, List

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QSlider, QGroupBox, QGridLayout
)
from PyQt6.QtCore import Qt, QPoint, QRect, pyqtSignal
from PyQt6.QtGui import QImage, QPixmap, QPainter, QPen, QColor


class ScreenshotLabel(QLabel):
    """스크린샷 위에 드래그로 바운딩 박스 선택"""
    roi_selected = pyqtSignal(tuple)

    def __init__(self, frame: np.ndarray):
        super().__init__()
        self.frame = frame
        self.h, self.w = frame.shape[:2]
        self._dragging = False
        self._start = QPoint()
        self._end = QPoint()
        self._current_rect: Optional[QRect] = None
        self._sc_ox = 0
        self._sc_oy = 0
        self._sc_pw = 1
        self._sc_ph = 1
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self._render()

    def _render(self):
        h, w, ch = self.frame.shape
        img = QImage(self.frame.tobytes(), w, h, ch * w, QImage.Format.Format_RGB888)
        pixmap = QPixmap.fromImage(img)
        if self._dragging and self._current_rect:
            painter = QPainter(pixmap)
            painter.setPen(QPen(QColor(255, 50, 50), 2))
            painter.drawRect(self._current_rect)
            r = self._current_rect
            ov = QColor(0, 0, 0, 80)
            painter.fillRect(0, 0, w, r.top(), ov)
            painter.fillRect(0, r.bottom(), w, h - r.bottom(), ov)
            painter.fillRect(0, r.top(), r.left(), r.height(), ov)
            painter.fillRect(r.right(), r.top(), w - r.right(), r.height(), ov)
            painter.end()
        scaled = pixmap.scaled(self.size(), Qt.AspectRatioMode.KeepAspectRatio,
                               Qt.TransformationMode.SmoothTransformation)
        self._sc_pw = max(scaled.width(), 1)
        self._sc_ph = max(scaled.height(), 1)
        self._sc_ox = (self.width() - self._sc_pw) // 2
        self._sc_oy = (self.height() - self._sc_ph) // 2
        self.setPixmap(scaled)

    def _to_image(self, pos: QPoint) -> QPoint:
        ix = int((pos.x() - self._sc_ox) * self.w / self._sc_pw)
        iy = int((pos.y() - self._sc_oy) * self.h / self._sc_ph)
        return QPoint(max(0, min(ix, self.w - 1)), max(0, min(iy, self.h - 1)))

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self._start = self._to_image(e.position().toPoint())
            self._end = self._start

    def mouseMoveEvent(self, e):
        if self._dragging:
            self._end = self._to_image(e.position().toPoint())
            self._current_rect = QRect(self._start, self._end).normalized()
            self._render()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self._dragging:
            self._dragging = False
            self._end = self._to_image(e.position().toPoint())
            rect = QRect(self._start, self._end).normalized()
            self._current_rect = None
            self._render()
            if rect.width() >= 5 and rect.height() >= 5:
                self.roi_selected.emit((rect.x(), rect.y(), rect.width(), rect.height()))

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._render()


class TrackingSetupDialog(QDialog):
    """추적 셋팅 다이얼로그 — 기존 프리셋 유지 + 추가/삭제"""

    MAX_PRESETS = 8

    def __init__(self, frame: np.ndarray, existing_crops: List[np.ndarray] = None, parent=None):
        """
        Args:
            frame: 현재 스크린샷 (RGB)
            existing_crops: 기존 프리셋 크롭 이미지 리스트 (RGB)
        """
        super().__init__(parent)
        self.setWindowTitle("추적 셋팅 — 유닛 프리셋 등록 (최대 8개)")
        self.setMinimumSize(900, 650)

        self.frame = frame
        # 크롭 이미지 저장 (RGB)
        self._crop_images: List[np.ndarray] = list(existing_crops) if existing_crops else []
        self._new_rois: List[Tuple[int, int, int, int]] = []  # 이번 세션에서 새로 추가된 ROI

        layout = QVBoxLayout()

        info = QLabel("추적할 유닛을 드래그로 크롭하세요 (드래그할 때마다 추가, 클릭으로 삭제)")
        info.setAlignment(Qt.AlignmentFlag.AlignCenter)
        info.setStyleSheet("color: #ccc; font-size: 13px; padding: 5px;")
        layout.addWidget(info)

        top_layout = QHBoxLayout()

        self.screenshot = ScreenshotLabel(frame)
        self.screenshot.setMinimumSize(600, 450)
        self.screenshot.roi_selected.connect(self._add_crop)
        top_layout.addWidget(self.screenshot, 4)

        # 프리셋 미리보기 패널
        preset_group = QGroupBox(f"프리셋 ({len(self._crop_images)}/8)")
        self._preset_group = preset_group
        preset_inner = QVBoxLayout()
        self.preset_grid = QGridLayout()
        self.preset_grid.setSpacing(4)

        self._slot_widgets: List[QPushButton] = []
        for i in range(self.MAX_PRESETS):
            btn = QPushButton()
            btn.setFixedSize(75, 75)
            btn.setStyleSheet("background-color: #1a1a1a; border: 1px solid #444;")
            btn.setToolTip("클릭하여 삭제")
            btn.clicked.connect(lambda _, idx=i: self._remove_crop(idx))
            self.preset_grid.addWidget(btn, i // 2, i % 2)
            self._slot_widgets.append(btn)

        preset_inner.addLayout(self.preset_grid)

        btn_clear = QPushButton("전체 초기화")
        btn_clear.clicked.connect(self._clear_all)
        preset_inner.addWidget(btn_clear)
        preset_inner.addStretch()
        preset_group.setLayout(preset_inner)
        preset_group.setFixedWidth(210)
        top_layout.addWidget(preset_group)

        layout.addLayout(top_layout, 1)

        # 임계값
        slider_layout = QHBoxLayout()
        slider_layout.addWidget(QLabel("일치 임계값:"))
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(10, 95)
        self.slider.setValue(50)
        self.slider.valueChanged.connect(lambda v: self._thr_label.setText(f"{v / 100:.2f}"))
        slider_layout.addWidget(self.slider)
        self._thr_label = QLabel("0.50")
        slider_layout.addWidget(self._thr_label)
        layout.addLayout(slider_layout)

        # 버튼
        btn_layout = QHBoxLayout()
        self.btn_confirm = QPushButton("적용")
        self.btn_confirm.setStyleSheet("background-color: #cc3333; color: white; padding: 8px 20px;")
        self.btn_confirm.clicked.connect(self.accept)
        self.btn_confirm.setEnabled(len(self._crop_images) > 0)
        btn_cancel = QPushButton("취소")
        btn_cancel.clicked.connect(self.reject)
        btn_layout.addStretch()
        btn_layout.addWidget(btn_cancel)
        btn_layout.addWidget(self.btn_confirm)
        layout.addLayout(btn_layout)

        self.setLayout(layout)

        # 기존 프리셋 미리보기 표시
        self._refresh_previews()

    def _add_crop(self, roi: tuple):
        if len(self._crop_images) >= self.MAX_PRESETS:
            return
        x, y, w, h = roi
        crop = self.frame[y:y+h, x:x+w].copy()
        self._crop_images.append(crop)
        self._new_rois.append(roi)
        self._refresh_previews()

    def _remove_crop(self, idx: int):
        if idx < len(self._crop_images):
            del self._crop_images[idx]
            # new_rois에서도 대응하는 것 제거 (인덱스 맞추기는 복잡하므로 전체 리셋)
            self._refresh_previews()

    def _clear_all(self):
        self._crop_images.clear()
        self._new_rois.clear()
        self._refresh_previews()

    def _refresh_previews(self):
        for i, btn in enumerate(self._slot_widgets):
            if i < len(self._crop_images):
                crop = self._crop_images[i]
                ch = crop.shape[2] if len(crop.shape) == 3 else 1
                h, w = crop.shape[:2]
                fmt = QImage.Format.Format_RGB888 if ch == 3 else QImage.Format.Format_Grayscale8
                img = QImage(crop.tobytes(), w, h, ch * w, fmt)
                pm = QPixmap.fromImage(img).scaled(
                    70, 70, Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation)
                btn.setIcon(pm)
                btn.setIconSize(pm.size())
                btn.setStyleSheet("background-color: #1a1a1a; border: 2px solid #4a9eff;")
            else:
                btn.setIcon(QPixmap())
                btn.setStyleSheet("background-color: #1a1a1a; border: 1px solid #444;")

        self._preset_group.setTitle(f"프리셋 ({len(self._crop_images)}/8)")
        self.btn_confirm.setEnabled(len(self._crop_images) > 0)

    def get_result(self) -> Optional[dict]:
        if self._crop_images:
            return {
                "crop_images": [c.copy() for c in self._crop_images],
                "threshold": self.slider.value() / 100.0
            }
        return None
