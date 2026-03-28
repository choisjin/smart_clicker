"""
추적 셋팅 다이얼로그 — 스크린샷 정지 + 드래그 박싱
"""

import numpy as np
from typing import Optional, Tuple

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QSlider
)
from PyQt6.QtCore import Qt, QPoint, QRect
from PyQt6.QtGui import QImage, QPixmap, QPainter, QPen, QColor


class ScreenshotLabel(QLabel):
    """스크린샷 위에 드래그로 바운딩 박스 선택"""

    def __init__(self, frame: np.ndarray):
        super().__init__()
        self.frame = frame
        self.h, self.w = frame.shape[:2]

        # 드래그 상태
        self._dragging = False
        self._start = QPoint()
        self._end = QPoint()
        self._roi: Optional[Tuple[int, int, int, int]] = None

        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self._update_display()

    def _update_display(self):
        """프레임 + 선택 박스 표시"""
        h, w, ch = self.frame.shape
        img = QImage(self.frame.data, w, h, ch * w, QImage.Format.Format_RGB888)
        pixmap = QPixmap.fromImage(img)

        # 선택 박스 그리기
        if self._roi or self._dragging:
            painter = QPainter(pixmap)
            pen = QPen(QColor(255, 50, 50), 2, Qt.PenStyle.SolidLine)
            painter.setPen(pen)

            if self._dragging:
                rect = QRect(self._start, self._end).normalized()
            elif self._roi:
                x, y, rw, rh = self._roi
                rect = QRect(x, y, rw, rh)
            else:
                rect = QRect()

            painter.drawRect(rect)

            # 반투명 외부 어둡게
            overlay = QColor(0, 0, 0, 100)
            painter.fillRect(0, 0, pixmap.width(), rect.top(), overlay)
            painter.fillRect(0, rect.bottom(), pixmap.width(), pixmap.height() - rect.bottom(), overlay)
            painter.fillRect(0, rect.top(), rect.left(), rect.height(), overlay)
            painter.fillRect(rect.right(), rect.top(), pixmap.width() - rect.right(), rect.height(), overlay)

            painter.end()

        # 위젯 크기에 맞게 스케일
        scaled = pixmap.scaled(self.size(), Qt.AspectRatioMode.KeepAspectRatio,
                               Qt.TransformationMode.SmoothTransformation)
        self.setPixmap(scaled)

    def _widget_to_image(self, pos: QPoint) -> QPoint:
        """위젯 좌표 → 원본 이미지 좌표"""
        pixmap = self.pixmap()
        if not pixmap:
            return pos

        pw, ph = pixmap.width(), pixmap.height()
        ww, wh = self.width(), self.height()
        ox = (ww - pw) // 2
        oy = (wh - ph) // 2

        ix = int((pos.x() - ox) * self.w / pw)
        iy = int((pos.y() - oy) * self.h / ph)
        ix = max(0, min(ix, self.w - 1))
        iy = max(0, min(iy, self.h - 1))
        return QPoint(ix, iy)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self._start = self._widget_to_image(event.position().toPoint())
            self._end = self._start
            self._roi = None

    def mouseMoveEvent(self, event):
        if self._dragging:
            self._end = self._widget_to_image(event.position().toPoint())
            self._update_display()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._dragging:
            self._dragging = False
            self._end = self._widget_to_image(event.position().toPoint())
            rect = QRect(self._start, self._end).normalized()
            if rect.width() >= 5 and rect.height() >= 5:
                self._roi = (rect.x(), rect.y(), rect.width(), rect.height())
            self._update_display()

    def get_roi(self) -> Optional[Tuple[int, int, int, int]]:
        return self._roi

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_display()


class TrackingSetupDialog(QDialog):
    """추적 셋팅 다이얼로그"""

    def __init__(self, frame: np.ndarray, parent=None):
        super().__init__(parent)
        self.setWindowTitle("추적 셋팅 — 유닛을 드래그로 선택하세요")
        self.setMinimumSize(800, 600)

        self.frame = frame
        self.threshold = 0.65

        layout = QVBoxLayout()

        # 안내 라벨
        info = QLabel("추적할 유닛을 최대한 타이트하게 드래그해서 박싱하세요")
        info.setAlignment(Qt.AlignmentFlag.AlignCenter)
        info.setStyleSheet("color: #ccc; font-size: 13px; padding: 5px;")
        layout.addWidget(info)

        # 스크린샷 + 드래그 영역
        self.screenshot = ScreenshotLabel(frame)
        self.screenshot.setMinimumSize(640, 480)
        layout.addWidget(self.screenshot, 1)

        # 임계값 슬라이더
        slider_layout = QHBoxLayout()
        slider_layout.addWidget(QLabel("일치 임계값:"))
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(30, 95)
        self.slider.setValue(65)
        self.slider.valueChanged.connect(lambda v: self.threshold_label.setText(f"{v / 100:.2f}"))
        slider_layout.addWidget(self.slider)
        self.threshold_label = QLabel("0.65")
        slider_layout.addWidget(self.threshold_label)
        layout.addLayout(slider_layout)

        # 버튼
        btn_layout = QHBoxLayout()
        self.btn_confirm = QPushButton("추적 시작")
        self.btn_confirm.setStyleSheet("background-color: #cc3333; color: white; padding: 8px 20px;")
        self.btn_confirm.clicked.connect(self.accept)
        self.btn_cancel = QPushButton("취소")
        self.btn_cancel.clicked.connect(self.reject)
        btn_layout.addStretch()
        btn_layout.addWidget(self.btn_cancel)
        btn_layout.addWidget(self.btn_confirm)
        layout.addLayout(btn_layout)

        self.setLayout(layout)

    def get_result(self) -> Optional[dict]:
        """선택 결과 반환 {roi, threshold}"""
        roi = self.screenshot.get_roi()
        if roi:
            return {
                "roi": roi,
                "threshold": self.slider.value() / 100.0
            }
        return None
