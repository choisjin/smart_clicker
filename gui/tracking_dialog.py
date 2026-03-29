"""
추적 셋팅 다이얼로그 — 스크린샷 정지 + 8방향 프리셋 드래그 박싱
기존 프리셋 유지 + 개별 삭제 + 저장/불러오기
"""

import os
import json
import numpy as np
from pathlib import Path
from typing import Optional, Tuple, List

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QSlider, QGroupBox, QGridLayout, QInputDialog, QComboBox, QMessageBox
)
from PyQt6.QtCore import Qt, QPoint, QRect, pyqtSignal
from PyQt6.QtGui import QImage, QPixmap, QPainter, QPen, QColor, QIcon

PRESETS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tracking_presets")


def save_preset(name: str, crops: List[np.ndarray], threshold: float,
                exclude_rect: tuple = None):
    """프리셋 저장 (크롭 이미지 PNG + 메타 JSON)"""
    from PIL import Image
    preset_dir = os.path.join(PRESETS_DIR, name)
    os.makedirs(preset_dir, exist_ok=True)

    for f in Path(preset_dir).glob("*.png"):
        f.unlink()

    for i, crop in enumerate(crops):
        # PIL로 저장 (한글 경로 지원)
        Image.fromarray(crop).save(os.path.join(preset_dir, f"{i}.png"))

    meta = {"threshold": threshold, "count": len(crops)}
    if exclude_rect:
        meta["exclude_rect"] = list(exclude_rect)
    with open(os.path.join(preset_dir, "meta.json"), "w") as f:
        json.dump(meta, f)


def load_preset(name: str) -> Optional[dict]:
    """프리셋 불러오기 → {crops: [RGB ndarray], threshold: float}"""
    from PIL import Image
    preset_dir = os.path.join(PRESETS_DIR, name)
    meta_path = os.path.join(preset_dir, "meta.json")
    if not os.path.exists(meta_path):
        return None

    with open(meta_path) as f:
        meta = json.load(f)

    crops = []
    for i in range(meta["count"]):
        img_path = os.path.join(preset_dir, f"{i}.png")
        if os.path.exists(img_path):
            img = np.array(Image.open(img_path).convert("RGB"))
            crops.append(img)

    result = {"crops": crops, "threshold": meta["threshold"]}
    if "exclude_rect" in meta:
        result["exclude_rect"] = tuple(meta["exclude_rect"])
    return result


def list_presets() -> List[str]:
    """저장된 프리셋 이름 목록"""
    if not os.path.exists(PRESETS_DIR):
        return []
    return sorted([d for d in os.listdir(PRESETS_DIR)
                    if os.path.isdir(os.path.join(PRESETS_DIR, d))
                    and os.path.exists(os.path.join(PRESETS_DIR, d, "meta.json"))])


def delete_preset(name: str):
    """프리셋 삭제"""
    import shutil
    preset_dir = os.path.join(PRESETS_DIR, name)
    if os.path.exists(preset_dir):
        shutil.rmtree(preset_dir)


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

    def __init__(self, frame: np.ndarray, existing_crops: List[np.ndarray] = None,
                 exclude_rect: Tuple[int, int, int, int] = None,
                 verify_click: np.ndarray = None, verify_transition: np.ndarray = None,
                 parent=None):
        """
        Args:
            frame: 현재 스크린샷 (RGB)
            existing_crops: 기존 프리셋 크롭 이미지 리스트 (RGB)
            exclude_rect: 내 캐릭터 제외 영역 (x, y, w, h)
            verify_click: 우클릭 성공 확인 이미지 (RGB)
            verify_transition: 화면 전환 확인 이미지 (RGB)
        """
        super().__init__(parent)
        self.setWindowTitle("추적 셋팅 — 유닛 프리셋 등록 (최대 8개)")
        self.setMinimumSize(900, 650)

        self.frame = frame
        self._crop_images: List[np.ndarray] = list(existing_crops) if existing_crops else []
        self._new_rois: List[Tuple[int, int, int, int]] = []
        self._exclude_rect: Optional[Tuple[int, int, int, int]] = exclude_rect
        self._setting_exclude = False  # 제외 영역 지정 모드
        self._setting_verify: Optional[str] = None  # "click" or "transition"
        self._verify_click: Optional[np.ndarray] = verify_click
        self._verify_transition: Optional[np.ndarray] = verify_transition

        layout = QVBoxLayout()

        info = QLabel("추적할 유닛을 드래그로 크롭하세요 (드래그할 때마다 추가, 클릭으로 삭제)")
        info.setAlignment(Qt.AlignmentFlag.AlignCenter)
        info.setStyleSheet("color: #ccc; font-size: 13px; padding: 5px;")
        layout.addWidget(info)

        top_layout = QHBoxLayout()

        self.screenshot = ScreenshotLabel(frame)
        self.screenshot.setMinimumSize(600, 450)
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

        # 저장/불러오기
        save_load_group = QGroupBox("프리셋 파일")
        sl_layout = QVBoxLayout()

        # 불러오기 콤보박스
        self._preset_combo = QComboBox()
        self._refresh_preset_list()
        sl_layout.addWidget(self._preset_combo)

        sl_btn_layout = QHBoxLayout()
        btn_load = QPushButton("불러오기")
        btn_load.clicked.connect(self._load_preset)
        btn_save = QPushButton("저장")
        btn_save.clicked.connect(self._save_preset)
        btn_del = QPushButton("삭제")
        btn_del.clicked.connect(self._delete_preset)
        sl_btn_layout.addWidget(btn_load)
        sl_btn_layout.addWidget(btn_save)
        sl_btn_layout.addWidget(btn_del)
        sl_layout.addLayout(sl_btn_layout)

        save_load_group.setLayout(sl_layout)
        preset_inner.addWidget(save_load_group)
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

        # 내 캐릭터 제외 영역
        exclude_layout = QHBoxLayout()
        self._btn_exclude = QPushButton("내 캐릭터 영역 지정")
        self._btn_exclude.setCheckable(True)
        self._btn_exclude.setStyleSheet("padding: 4px;")
        self._btn_exclude.toggled.connect(self._toggle_exclude_mode)
        exclude_layout.addWidget(self._btn_exclude)
        self._exclude_label = QLabel(self._format_exclude())
        self._exclude_label.setStyleSheet("color: #aaa; font-size: 11px;")
        exclude_layout.addWidget(self._exclude_label)
        btn_exclude_clear = QPushButton("해제")
        btn_exclude_clear.setFixedWidth(40)
        btn_exclude_clear.clicked.connect(self._clear_exclude)
        exclude_layout.addWidget(btn_exclude_clear)
        layout.addLayout(exclude_layout)

        # 확인 이미지 (우클릭 성공 / 화면 전환)
        verify_group = QGroupBox("확인 이미지 (선택사항)")
        verify_layout = QHBoxLayout()

        # 우클릭 성공 확인
        self._btn_verify_click = QPushButton("우클릭 성공 확인")
        self._btn_verify_click.setCheckable(True)
        self._btn_verify_click.setStyleSheet("padding: 4px;")
        self._btn_verify_click.toggled.connect(lambda on: self._toggle_verify_mode("click", on))
        verify_layout.addWidget(self._btn_verify_click)
        self._verify_click_preview = QPushButton()
        self._verify_click_preview.setFixedSize(60, 60)
        self._verify_click_preview.setToolTip("클릭하여 삭제")
        self._verify_click_preview.clicked.connect(lambda: self._clear_verify("click"))
        verify_layout.addWidget(self._verify_click_preview)

        verify_layout.addSpacing(20)

        # 화면 전환 확인
        self._btn_verify_trans = QPushButton("화면 전환 확인")
        self._btn_verify_trans.setCheckable(True)
        self._btn_verify_trans.setStyleSheet("padding: 4px;")
        self._btn_verify_trans.toggled.connect(lambda on: self._toggle_verify_mode("transition", on))
        verify_layout.addWidget(self._btn_verify_trans)
        self._verify_trans_preview = QPushButton()
        self._verify_trans_preview.setFixedSize(60, 60)
        self._verify_trans_preview.setToolTip("클릭하여 삭제")
        self._verify_trans_preview.clicked.connect(lambda: self._clear_verify("transition"))
        verify_layout.addWidget(self._verify_trans_preview)

        verify_layout.addStretch()
        verify_group.setLayout(verify_layout)
        layout.addWidget(verify_group)

        self._refresh_verify_previews()

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

        # 스크린샷 드래그 → 제외 영역 or 크롭
        self.screenshot.roi_selected.connect(self._on_roi_selected)

        # 기존 프리셋 미리보기 표시
        self._refresh_previews()

    def _format_exclude(self) -> str:
        if self._exclude_rect:
            x, y, w, h = self._exclude_rect
            return f"({x},{y}) {w}x{h}"
        return "미설정"

    def _toggle_exclude_mode(self, on: bool):
        self._setting_exclude = on
        if on:
            self._btn_exclude.setText("화면에서 드래그하세요...")
            self._btn_exclude.setStyleSheet("background-color: #3366cc; color: white; padding: 4px;")
        else:
            self._btn_exclude.setText("내 캐릭터 영역 지정")
            self._btn_exclude.setStyleSheet("padding: 4px;")

    def _clear_exclude(self):
        self._exclude_rect = None
        self._exclude_label.setText(self._format_exclude())

    def _toggle_verify_mode(self, kind: str, on: bool):
        """확인 이미지 지정 모드 토글"""
        btn = self._btn_verify_click if kind == "click" else self._btn_verify_trans
        other = self._btn_verify_trans if kind == "click" else self._btn_verify_click
        if on:
            self._setting_verify = kind
            self._setting_exclude = False
            self._btn_exclude.setChecked(False)
            other.setChecked(False)
            btn.setText("화면에서 드래그하세요...")
            btn.setStyleSheet("background-color: #cc6600; color: white; padding: 4px;")
        else:
            self._setting_verify = None
            label = "우클릭 성공 확인" if kind == "click" else "화면 전환 확인"
            btn.setText(label)
            btn.setStyleSheet("padding: 4px;")

    def _clear_verify(self, kind: str):
        """확인 이미지 삭제"""
        if kind == "click":
            self._verify_click = None
        else:
            self._verify_transition = None
        self._refresh_verify_previews()

    def _refresh_verify_previews(self):
        """확인 이미지 미리보기 갱신"""
        for img, btn in [
            (self._verify_click, self._verify_click_preview),
            (self._verify_transition, self._verify_trans_preview),
        ]:
            if img is not None:
                h, w = img.shape[:2]
                ch = img.shape[2] if len(img.shape) == 3 else 1
                fmt = QImage.Format.Format_RGB888 if ch == 3 else QImage.Format.Format_Grayscale8
                qimg = QImage(img.tobytes(), w, h, ch * w, fmt)
                pm = QPixmap.fromImage(qimg).scaled(
                    55, 55, Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation)
                btn.setIcon(QIcon(pm))
                btn.setIconSize(pm.size())
                btn.setStyleSheet("background-color: #1a1a1a; border: 2px solid #cc6600;")
            else:
                btn.setIcon(QIcon())
                btn.setStyleSheet("background-color: #1a1a1a; border: 1px solid #444;")

    def _on_roi_selected(self, roi: tuple):
        """드래그 완료 → 모드에 따라 제외 영역/확인 이미지/크롭 추가"""
        if self._setting_verify:
            x, y, w, h = roi
            crop = self.frame[y:y+h, x:x+w].copy()
            if self._setting_verify == "click":
                self._verify_click = crop
                self._btn_verify_click.setChecked(False)
                print(f"[추적 셋팅] 우클릭 성공 확인 이미지 설정: {roi}")
            else:
                self._verify_transition = crop
                self._btn_verify_trans.setChecked(False)
                print(f"[추적 셋팅] 화면 전환 확인 이미지 설정: {roi}")
            self._refresh_verify_previews()
        elif self._setting_exclude:
            self._exclude_rect = roi
            self._exclude_label.setText(self._format_exclude())
            self._btn_exclude.setChecked(False)
            print(f"[추적 셋팅] 제외 영역 설정: {roi}")
        else:
            self._add_crop(roi)

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
                btn.setIcon(QIcon(pm))
                btn.setIconSize(pm.size())
                btn.setStyleSheet("background-color: #1a1a1a; border: 2px solid #4a9eff;")
            else:
                btn.setIcon(QIcon())
                btn.setStyleSheet("background-color: #1a1a1a; border: 1px solid #444;")

        self._preset_group.setTitle(f"프리셋 ({len(self._crop_images)}/8)")
        self.btn_confirm.setEnabled(len(self._crop_images) > 0)

    def _refresh_preset_list(self):
        """저장된 프리셋 목록 갱신"""
        self._preset_combo.clear()
        names = list_presets()
        if names:
            self._preset_combo.addItems(names)
        else:
            self._preset_combo.addItem("(저장된 프리셋 없음)")

    def _save_preset(self):
        """현재 크롭을 프리셋으로 저장"""
        if not self._crop_images:
            QMessageBox.warning(self, "저장 실패", "크롭 이미지가 없습니다.")
            return
        name, ok = QInputDialog.getText(self, "프리셋 저장", "프리셋 이름:")
        if ok and name.strip():
            save_preset(name.strip(), self._crop_images, self.slider.value() / 100.0,
                       self._exclude_rect)
            self._refresh_preset_list()
            self._preset_combo.setCurrentText(name.strip())
            print(f"[프리셋] '{name.strip()}' 저장 완료 ({len(self._crop_images)}개 크롭)")

    def _load_preset(self):
        """프리셋 불러오기"""
        name = self._preset_combo.currentText()
        if not name or name.startswith("("):
            return
        data = load_preset(name)
        if data:
            self._crop_images = data["crops"]
            self._new_rois.clear()
            self.slider.setValue(int(data["threshold"] * 100))
            self._exclude_rect = data.get("exclude_rect")
            self._exclude_label.setText(self._format_exclude())
            self._refresh_previews()
            print(f"[프리셋] '{name}' 불러오기 완료 ({len(data['crops'])}개 크롭, 임계값 {data['threshold']:.2f})")

    def _delete_preset(self):
        """프리셋 삭제"""
        name = self._preset_combo.currentText()
        if not name or name.startswith("("):
            return
        reply = QMessageBox.question(self, "프리셋 삭제", f"'{name}' 프리셋을 삭제하시겠습니까?")
        if reply == QMessageBox.StandardButton.Yes:
            delete_preset(name)
            self._refresh_preset_list()
            print(f"[프리셋] '{name}' 삭제됨")

    def get_result(self) -> Optional[dict]:
        if self._crop_images:
            result = {
                "crop_images": [c.copy() for c in self._crop_images],
                "threshold": self.slider.value() / 100.0,
                "exclude_rect": self._exclude_rect,
            }
            if self._verify_click is not None:
                result["verify_click"] = self._verify_click.copy()
            if self._verify_transition is not None:
                result["verify_transition"] = self._verify_transition.copy()
            return result
        return None
