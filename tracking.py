"""
Unit Tracker - 유닛 특징 추출 + 실시간 매칭
관제 PC에서 실행 (스트리밍 프레임 기반)

사용법:
    tracker = UnitTracker()
    tracker.set_target(frame, (x, y, w, h))  # 드래그 영역
    matches = tracker.find_matches(frame)     # [(x, y, w, h, score), ...]
"""

import numpy as np
from typing import List, Tuple, Optional
from dataclasses import dataclass

try:
    import cv2
except ImportError:
    print("opencv-python 설치 필요: pip install opencv-python")
    exit(1)


@dataclass
class TrackingProfile:
    """추적 대상 특징 프로파일"""
    template: np.ndarray  # BGR 템플릿 이미지
    hsv_hist: np.ndarray  # HSV 색상 히스토그램
    width: int
    height: int
    aspect_ratio: float


@dataclass
class TrackingMatch:
    """매칭 결과"""
    x: int
    y: int
    w: int
    h: int
    score: float  # 0.0 ~ 1.0


class UnitTracker:
    """유닛 추적기 — 특징 추출 + 프레임 매칭"""

    def __init__(self):
        self.profile: Optional[TrackingProfile] = None
        self.match_threshold: float = 0.65
        self.template_weight: float = 0.6
        self.histogram_weight: float = 0.4
        self.scale_range: Tuple[float, float] = (0.8, 1.2)
        self.scale_steps: int = 5
        self.nms_threshold: float = 0.3

    def set_target(self, frame: np.ndarray, roi: Tuple[int, int, int, int]):
        """추적 대상 설정 (프레임 + 선택 영역)

        Args:
            frame: BGR 또는 RGB 프레임
            roi: (x, y, w, h) 선택 영역
        """
        x, y, w, h = roi
        if w < 5 or h < 5:
            return

        template = frame[y:y+h, x:x+w].copy()

        # HSV 히스토그램 추출
        hsv = cv2.cvtColor(template, cv2.COLOR_BGR2HSV) if len(template.shape) == 3 else template
        hist = cv2.calcHist([hsv], [0, 1], None, [30, 32], [0, 180, 0, 256])
        cv2.normalize(hist, hist, 0, 1, cv2.NORM_MINMAX)

        self.profile = TrackingProfile(
            template=template,
            hsv_hist=hist,
            width=w,
            height=h,
            aspect_ratio=w / h
        )

    def has_target(self) -> bool:
        return self.profile is not None

    def clear_target(self):
        self.profile = None

    def find_matches(self, frame: np.ndarray) -> List[TrackingMatch]:
        """프레임에서 추적 대상 매칭

        Args:
            frame: BGR 또는 RGB 프레임

        Returns:
            매칭 결과 리스트 (score 내림차순)
        """
        if not self.profile:
            return []

        candidates = []

        # 1. 멀티스케일 템플릿 매칭
        template_candidates = self._template_match(frame)
        candidates.extend(template_candidates)

        # 2. 히스토그램 역투영 기반 매칭
        hist_candidates = self._histogram_match(frame)
        candidates.extend(hist_candidates)

        if not candidates:
            return []

        # 3. NMS로 중복 제거
        matches = self._nms(candidates)

        # 4. 임계값 필터 + 정렬
        matches = [m for m in matches if m.score >= self.match_threshold]
        matches.sort(key=lambda m: m.score, reverse=True)

        return matches

    def find_nearest_to_center(self, frame: np.ndarray) -> Optional[TrackingMatch]:
        """화면 중앙에서 가장 가까운 매칭 유닛 반환"""
        matches = self.find_matches(frame)
        if not matches:
            return None

        h, w = frame.shape[:2]
        cx, cy = w // 2, h // 2

        def dist_to_center(m: TrackingMatch) -> float:
            mx = m.x + m.w // 2
            my = m.y + m.h // 2
            return ((mx - cx) ** 2 + (my - cy) ** 2) ** 0.5

        return min(matches, key=dist_to_center)

    def _template_match(self, frame: np.ndarray) -> List[TrackingMatch]:
        """멀티스케일 템플릿 매칭"""
        results = []
        template = self.profile.template
        th, tw = template.shape[:2]

        # 그레이스케일 변환
        if len(frame.shape) == 3:
            frame_gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            tmpl_gray = cv2.cvtColor(template, cv2.COLOR_BGR2GRAY)
        else:
            frame_gray = frame
            tmpl_gray = template

        scales = np.linspace(self.scale_range[0], self.scale_range[1], self.scale_steps)

        for scale in scales:
            sw = max(5, int(tw * scale))
            sh = max(5, int(th * scale))

            if sw >= frame_gray.shape[1] or sh >= frame_gray.shape[0]:
                continue

            scaled_tmpl = cv2.resize(tmpl_gray, (sw, sh))
            result = cv2.matchTemplate(frame_gray, scaled_tmpl, cv2.TM_CCOEFF_NORMED)

            # 임계값 이상인 위치 추출
            locations = np.where(result >= self.match_threshold * 0.8)
            for py, px in zip(*locations):
                score = float(result[py, px]) * self.template_weight
                results.append(TrackingMatch(
                    x=px, y=py, w=sw, h=sh, score=score
                ))

        return results

    def _histogram_match(self, frame: np.ndarray) -> List[TrackingMatch]:
        """히스토그램 역투영 기반 매칭"""
        results = []
        if len(frame.shape) != 3:
            return results

        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        back_proj = cv2.calcBackProject([hsv], [0, 1], self.profile.hsv_hist,
                                         [0, 180, 0, 256], 1)

        # 노이즈 제거
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
        back_proj = cv2.morphologyEx(back_proj, cv2.MORPH_CLOSE, kernel)
        back_proj = cv2.morphologyEx(back_proj, cv2.MORPH_OPEN, kernel)

        # 이진화
        _, thresh = cv2.threshold(back_proj, 50, 255, cv2.THRESH_BINARY)

        # 윤곽선 검출
        contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        tw, th = self.profile.width, self.profile.height
        min_area = tw * th * 0.3
        max_area = tw * th * 3.0

        for cnt in contours:
            area = cv2.contourArea(cnt)
            if min_area < area < max_area:
                bx, by, bw, bh = cv2.boundingRect(cnt)
                # 종횡비 체크
                ar = bw / max(bh, 1)
                if 0.3 < ar / self.profile.aspect_ratio < 3.0:
                    # 해당 영역의 역투영 평균값으로 score
                    region = back_proj[by:by+bh, bx:bx+bw]
                    score = float(np.mean(region) / 255.0) * self.histogram_weight
                    results.append(TrackingMatch(
                        x=bx, y=by, w=bw, h=bh, score=score
                    ))

        return results

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
