"""
Fast Unit Tracker - 색상 양자화 기반 초고속 유닛 추적
관제 PC에서 실행 (스트리밍 프레임 기반)

방식:
  1. 프리셋(최대 8개)에서 유닛 고유 색상 추출 (양자화)
  2. 프레임에서 고유 색상 매칭 → 바이너리 마스크 (numpy 벡터 연산)
  3. 윤곽선 → 크기 필터 → 바운딩 박스
  ※ 템플릿 매칭 없이 O(pixels)로 동작 → 60FPS 가능
"""

import numpy as np
from typing import List, Tuple, Optional, Set
from dataclasses import dataclass, field

try:
    import cv2
except ImportError:
    print("opencv-python 설치 필요: pip install opencv-python")
    exit(1)


@dataclass
class TrackingMatch:
    """매칭 결과"""
    x: int
    y: int
    w: int
    h: int
    score: float  # 0.0 ~ 1.0


@dataclass
class Preset:
    """프리셋 (8방향 중 하나)"""
    template: np.ndarray  # 원본 크롭 이미지 (BGR)
    width: int
    height: int
    area: int
    color_set: Set[int] = field(default_factory=set)  # 양자화된 고유 색상 세트


class FastUnitTracker:
    """초고속 유닛 추적기 — 색상 양자화 LUT 기반"""

    # 양자화 비트 시프트 (4 = RGB 각 16단계 = 4096 색상)
    QUANT_SHIFT = 4

    def __init__(self):
        self.presets: List[Preset] = []  # 최대 8개
        self.match_threshold: float = 0.65
        self.nms_threshold: float = 0.3

        # 양자화 색상 LUT (프리셋의 고유 색상 합집합)
        self._color_lut: Optional[np.ndarray] = None  # shape: (4096,) bool
        self._avg_area: int = 0  # 프리셋 평균 면적
        self._min_area: int = 0
        self._max_area: int = 0

    @property
    def max_presets(self) -> int:
        return 8

    def has_target(self) -> bool:
        return len(self.presets) > 0

    def clear(self):
        self.presets.clear()
        self._color_lut = None

    def get_crop_images_rgb(self) -> List[np.ndarray]:
        """현재 프리셋 크롭 이미지를 RGB로 반환 (다이얼로그 연동용)"""
        result = []
        for p in self.presets:
            rgb = p.template[:, :, ::-1].copy()  # BGR→RGB
            result.append(rgb)
        return result

    def add_preset(self, frame_bgr: np.ndarray, roi: Tuple[int, int, int, int]) -> int:
        """프리셋 추가 (최대 8개). 추가된 프리셋 인덱스 반환."""
        if len(self.presets) >= self.max_presets:
            return -1

        x, y, w, h = roi
        if w < 3 or h < 3:
            return -1

        crop = frame_bgr[y:y+h, x:x+w].copy()

        # 양자화된 고유 색상 추출
        color_set = self._extract_unique_colors(crop)

        preset = Preset(
            template=crop,
            width=w,
            height=h,
            area=w * h,
            color_set=color_set
        )
        self.presets.append(preset)

        # LUT 재구성
        self._rebuild_lut()

        return len(self.presets) - 1

    def add_preset_from_crop(self, crop_bgr: np.ndarray) -> int:
        """크롭 이미지(BGR)로 프리셋 추가"""
        if len(self.presets) >= self.max_presets:
            return -1
        h, w = crop_bgr.shape[:2]
        if w < 3 or h < 3:
            return -1
        color_set = self._extract_unique_colors(crop_bgr)
        preset = Preset(
            template=crop_bgr.copy(),
            width=w, height=h, area=w * h,
            color_set=color_set
        )
        self.presets.append(preset)
        self._rebuild_lut()
        return len(self.presets) - 1

    def remove_preset(self, idx: int):
        if 0 <= idx < len(self.presets):
            del self.presets[idx]
            self._rebuild_lut()

    def _extract_unique_colors(self, crop_bgr: np.ndarray) -> Set[int]:
        """크롭 이미지에서 양자화된 색상 추출 (배경 제거)"""
        h, w = crop_bgr.shape[:2]

        # 양자화: RGB 각 채널을 QUANT_SHIFT 비트 줄임
        q = crop_bgr >> self.QUANT_SHIFT  # (h, w, 3), 각 0~15

        # 단일 정수로 인코딩: r*256 + g*16 + b (4096 범위)
        encoded = q[:, :, 2].astype(np.int32) * 256 + q[:, :, 1].astype(np.int32) * 16 + q[:, :, 0].astype(np.int32)

        # 테두리 1px 색상 = 배경으로 간주하여 제거
        bg_colors = set()
        if h > 2 and w > 2:
            border = np.concatenate([
                encoded[0, :], encoded[-1, :],  # 상단, 하단
                encoded[:, 0], encoded[:, -1]   # 좌측, 우측
            ])
            bg_colors = set(border.tolist())

        # 내부 색상에서 배경 색상 제거
        all_colors = set(encoded.ravel().tolist())
        unique = all_colors - bg_colors

        return unique

    def _rebuild_lut(self):
        """모든 프리셋의 고유 색상을 합쳐 LUT 구성"""
        if not self.presets:
            self._color_lut = None
            return

        # 합집합
        all_colors = set()
        for p in self.presets:
            all_colors |= p.color_set

        # LUT: 4096 크기 bool 배열
        lut = np.zeros(4096, dtype=np.uint8)
        for c in all_colors:
            if 0 <= c < 4096:
                lut[c] = 255

        self._color_lut = lut

        # 면적 범위 계산
        areas = [p.area for p in self.presets]
        self._avg_area = int(np.mean(areas))
        self._min_area = int(min(areas) * 0.4)
        self._max_area = int(max(areas) * 2.5)

    def find_matches(self, frame_bgr: np.ndarray) -> List[TrackingMatch]:
        """프레임에서 유닛 매칭 (양자화 + 템플릿 매칭 병행)"""
        if not self.presets:
            return []

        candidates = []

        # 방법 1: 템플릿 매칭 (정확, 모든 프리셋에 대해)
        frame_gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
        for preset in self.presets:
            tmpl_gray = cv2.cvtColor(preset.template, cv2.COLOR_BGR2GRAY)
            th, tw = tmpl_gray.shape[:2]

            # 원본 + 0.9x + 1.1x 3단계 스케일
            for scale in [0.9, 1.0, 1.1]:
                sw = max(5, int(tw * scale))
                sh = max(5, int(th * scale))
                if sw >= frame_gray.shape[1] or sh >= frame_gray.shape[0]:
                    continue

                scaled_tmpl = cv2.resize(tmpl_gray, (sw, sh)) if scale != 1.0 else tmpl_gray
                result = cv2.matchTemplate(frame_gray, scaled_tmpl, cv2.TM_CCOEFF_NORMED)

                locations = np.where(result >= self.match_threshold)
                for py, px in zip(*locations):
                    score = float(result[py, px])
                    candidates.append(TrackingMatch(
                        x=px, y=py, w=sw, h=sh, score=round(score, 3)
                    ))

        # 방법 2: 양자화 LUT (보조, 색상 기반 — 후보 추가)
        if self._color_lut is not None:
            q = frame_bgr >> self.QUANT_SHIFT
            encoded = (q[:, :, 2].astype(np.int32) * 256 +
                       q[:, :, 1].astype(np.int32) * 16 +
                       q[:, :, 0].astype(np.int32))
            mask = self._color_lut[encoded]

            kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
            mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
            mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)

            contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            for cnt in contours:
                area = cv2.contourArea(cnt)
                if self._min_area < area < self._max_area:
                    bx, by, bw, bh = cv2.boundingRect(cnt)
                    roi_mask = mask[by:by+bh, bx:bx+bw]
                    density = np.count_nonzero(roi_mask) / max(bw * bh, 1)
                    if density >= self.match_threshold:
                        candidates.append(TrackingMatch(
                            x=bx, y=by, w=bw, h=bh, score=round(density * 0.8, 3)
                        ))

        # NMS
        if candidates:
            candidates = self._nms(candidates)

        candidates.sort(key=lambda m: m.score, reverse=True)
        return candidates

    def find_nearest_to_center(self, frame_bgr: np.ndarray) -> Optional[TrackingMatch]:
        """화면 중앙에서 가장 가까운 매칭 유닛"""
        matches = self.find_matches(frame_bgr)
        if not matches:
            return None

        h, w = frame_bgr.shape[:2]
        cx, cy = w // 2, h // 2

        return min(matches, key=lambda m:
            ((m.x + m.w // 2 - cx) ** 2 + (m.y + m.h // 2 - cy) ** 2))

    def _nms(self, candidates: List[TrackingMatch]) -> List[TrackingMatch]:
        """Non-Maximum Suppression"""
        if not candidates:
            return []

        boxes = np.array([[m.x, m.y, m.x + m.w, m.y + m.h] for m in candidates])
        scores = np.array([m.score for m in candidates])

        x1, y1, x2, y2 = boxes[:, 0], boxes[:, 1], boxes[:, 2], boxes[:, 3]
        areas = (x2 - x1) * (y2 - y1)
        order = scores.argsort()[::-1]

        keep = []
        while order.size > 0:
            i = order[0]
            keep.append(i)

            xx1 = np.maximum(x1[i], x1[order[1:]])
            yy1 = np.maximum(y1[i], y1[order[1:]])
            xx2 = np.minimum(x2[i], x2[order[1:]])
            yy2 = np.minimum(y2[i], y2[order[1:]])

            inter = np.maximum(0, xx2 - xx1) * np.maximum(0, yy2 - yy1)
            iou = inter / (areas[i] + areas[order[1:]] - inter + 1e-6)

            inds = np.where(iou <= self.nms_threshold)[0]
            order = order[inds + 1]

        return [candidates[i] for i in keep]
