"""
Target Finder - 이미지 템플릿 매칭
화면에서 타겟 이미지를 찾아 좌표 반환

사용법:
    from target_finder import TargetFinder

    finder = TargetFinder()
    matches = finder.find_template(frame, "button.png")
    if matches:
        x, y, w, h, confidence = matches[0]
"""

import os
from typing import List, Tuple, Optional
import numpy as np

try:
    import cv2
except ImportError:
    print("opencv-python 설치 필요: pip install opencv-python")
    exit(1)


class TargetFinder:
    """이미지 템플릿 매칭 클래스"""

    def __init__(self, template_dir: str = "templates"):
        """
        Args:
            template_dir: 템플릿 이미지 저장 디렉토리
        """
        self.template_dir = template_dir
        self.template_cache = {}  # 템플릿 캐시

        # 템플릿 디렉토리 생성
        if not os.path.exists(template_dir):
            os.makedirs(template_dir)
            print(f"[OK] 템플릿 디렉토리 생성: {template_dir}")

    def _load_template(self, template_path: str) -> Optional[np.ndarray]:
        """템플릿 이미지 로드 (캐싱)"""
        # 상대 경로면 template_dir 기준
        if not os.path.isabs(template_path):
            template_path = os.path.join(self.template_dir, template_path)

        # 캐시 확인
        if template_path in self.template_cache:
            return self.template_cache[template_path]

        # 파일 로드
        if not os.path.exists(template_path):
            print(f"[ERROR] 템플릿 파일 없음: {template_path}")
            return None

        template = cv2.imread(template_path)
        if template is None:
            print(f"[ERROR] 템플릿 로드 실패: {template_path}")
            return None

        self.template_cache[template_path] = template
        return template

    def find_template(self, frame: np.ndarray, template_path: str,
                      threshold: float = 0.8, method: int = cv2.TM_CCOEFF_NORMED
                      ) -> Optional[Tuple[int, int, int, int, float]]:
        """
        템플릿 매칭으로 단일 타겟 찾기

        Args:
            frame: 검색할 화면 이미지 (RGB 또는 BGR)
            template_path: 템플릿 이미지 경로
            threshold: 매칭 임계값 (0-1)
            method: OpenCV 매칭 메서드

        Returns:
            (x, y, width, height, confidence) 또는 None
        """
        template = self._load_template(template_path)
        if template is None:
            return None

        # RGB → BGR 변환 (필요시)
        if len(frame.shape) == 3 and frame.shape[2] == 3:
            # RGB인지 BGR인지 판단하기 어려우므로 둘 다 시도
            frame_bgr = frame
            if frame.dtype != np.uint8:
                frame_bgr = frame.astype(np.uint8)

        # 그레이스케일 변환
        gray_frame = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
        gray_template = cv2.cvtColor(template, cv2.COLOR_BGR2GRAY)

        # 템플릿 매칭
        result = cv2.matchTemplate(gray_frame, gray_template, method)
        min_val, max_val, min_loc, max_loc = cv2.minMaxLoc(result)

        # 매칭 결과 확인
        if method in [cv2.TM_SQDIFF, cv2.TM_SQDIFF_NORMED]:
            confidence = 1 - min_val
            loc = min_loc
        else:
            confidence = max_val
            loc = max_loc

        if confidence >= threshold:
            h, w = template.shape[:2]
            return (loc[0], loc[1], w, h, confidence)

        return None

    def find_all_templates(self, frame: np.ndarray, template_path: str,
                           threshold: float = 0.8, max_count: int = 10
                           ) -> List[Tuple[int, int, int, int, float]]:
        """
        모든 일치하는 템플릿 찾기

        Args:
            frame: 검색할 화면 이미지
            template_path: 템플릿 이미지 경로
            threshold: 매칭 임계값
            max_count: 최대 반환 개수

        Returns:
            [(x, y, width, height, confidence), ...]
        """
        template = self._load_template(template_path)
        if template is None:
            return []

        # BGR 변환
        if len(frame.shape) == 3:
            frame_bgr = frame.astype(np.uint8) if frame.dtype != np.uint8 else frame

        # 그레이스케일
        gray_frame = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
        gray_template = cv2.cvtColor(template, cv2.COLOR_BGR2GRAY)

        h, w = template.shape[:2]

        # 템플릿 매칭
        result = cv2.matchTemplate(gray_frame, gray_template, cv2.TM_CCOEFF_NORMED)

        # 임계값 이상인 위치 찾기
        locations = np.where(result >= threshold)
        matches = []

        for pt in zip(*locations[::-1]):  # (x, y) 순서로
            confidence = result[pt[1], pt[0]]
            matches.append((pt[0], pt[1], w, h, float(confidence)))

        # 중복 제거 (NMS - Non-Maximum Suppression)
        matches = self._non_max_suppression(matches, overlap_thresh=0.3)

        # 신뢰도 순 정렬
        matches.sort(key=lambda x: x[4], reverse=True)

        return matches[:max_count]

    def _non_max_suppression(self, matches: List[Tuple], overlap_thresh: float = 0.3
                             ) -> List[Tuple]:
        """중복 영역 제거 (NMS)"""
        if not matches:
            return []

        # 좌표 추출
        boxes = np.array([[m[0], m[1], m[0] + m[2], m[1] + m[3]] for m in matches])
        scores = np.array([m[4] for m in matches])

        # 정렬
        idxs = np.argsort(scores)[::-1]
        picked = []

        while len(idxs) > 0:
            i = idxs[0]
            picked.append(i)

            # IoU 계산
            xx1 = np.maximum(boxes[i, 0], boxes[idxs[1:], 0])
            yy1 = np.maximum(boxes[i, 1], boxes[idxs[1:], 1])
            xx2 = np.minimum(boxes[i, 2], boxes[idxs[1:], 2])
            yy2 = np.minimum(boxes[i, 3], boxes[idxs[1:], 3])

            w = np.maximum(0, xx2 - xx1)
            h = np.maximum(0, yy2 - yy1)

            overlap = (w * h) / ((boxes[idxs[1:], 2] - boxes[idxs[1:], 0]) *
                                 (boxes[idxs[1:], 3] - boxes[idxs[1:], 1]))

            idxs = idxs[np.where(overlap <= overlap_thresh)[0] + 1]

        return [matches[i] for i in picked]

    def find_template_multiscale(self, frame: np.ndarray, template_path: str,
                                 threshold: float = 0.8,
                                 scale_range: Tuple[float, float] = (0.5, 1.5),
                                 scale_steps: int = 10
                                 ) -> Optional[Tuple[int, int, int, int, float, float]]:
        """
        멀티스케일 템플릿 매칭 (크기 변화 대응)

        Args:
            frame: 검색할 화면 이미지
            template_path: 템플릿 이미지 경로
            threshold: 매칭 임계값
            scale_range: 스케일 범위 (min, max)
            scale_steps: 스케일 단계 수

        Returns:
            (x, y, width, height, confidence, scale) 또는 None
        """
        template = self._load_template(template_path)
        if template is None:
            return None

        # BGR 변환
        if len(frame.shape) == 3:
            frame_bgr = frame.astype(np.uint8) if frame.dtype != np.uint8 else frame

        gray_frame = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
        gray_template = cv2.cvtColor(template, cv2.COLOR_BGR2GRAY)

        best_match = None
        best_confidence = 0

        # 스케일 범위
        scales = np.linspace(scale_range[0], scale_range[1], scale_steps)

        for scale in scales:
            # 템플릿 리사이즈
            new_w = int(gray_template.shape[1] * scale)
            new_h = int(gray_template.shape[0] * scale)

            if new_w < 10 or new_h < 10:
                continue
            if new_w > gray_frame.shape[1] or new_h > gray_frame.shape[0]:
                continue

            resized = cv2.resize(gray_template, (new_w, new_h))

            # 매칭
            result = cv2.matchTemplate(gray_frame, resized, cv2.TM_CCOEFF_NORMED)
            _, max_val, _, max_loc = cv2.minMaxLoc(result)

            if max_val > best_confidence and max_val >= threshold:
                best_confidence = max_val
                best_match = (max_loc[0], max_loc[1], new_w, new_h, max_val, scale)

        return best_match

    def get_center(self, match: Tuple) -> Tuple[int, int]:
        """매칭 결과의 중심 좌표"""
        x, y, w, h = match[:4]
        return (x + w // 2, y + h // 2)

    def capture_template(self, frame: np.ndarray, x: int, y: int, w: int, h: int,
                         name: str) -> str:
        """화면에서 영역을 템플릿으로 저장"""
        # 영역 추출
        region = frame[y:y+h, x:x+w]

        # BGR 변환 (필요시)
        if len(region.shape) == 3 and region.shape[2] == 3:
            region_bgr = cv2.cvtColor(region, cv2.COLOR_RGB2BGR)
        else:
            region_bgr = region

        # 저장
        path = os.path.join(self.template_dir, f"{name}.png")
        cv2.imwrite(path, region_bgr)
        print(f"[OK] 템플릿 저장: {path}")

        return path

    def clear_cache(self):
        """템플릿 캐시 클리어"""
        self.template_cache.clear()


class SmartClicker:
    """TargetFinder + Controller 통합 클래스"""

    def __init__(self, controller, finder: Optional[TargetFinder] = None):
        """
        Args:
            controller: RemoteController 인스턴스
            finder: TargetFinder 인스턴스 (없으면 생성)
        """
        self.ctrl = controller
        self.finder = finder or TargetFinder()

    def click_template(self, agent_name: str, template_path: str,
                       threshold: float = 0.8, human_like: bool = True,
                       offset: Tuple[int, int] = (0, 0)) -> bool:
        """
        템플릿을 찾아서 클릭

        Args:
            agent_name: Agent 이름
            template_path: 템플릿 이미지 경로
            threshold: 매칭 임계값
            human_like: 사람처럼 동작
            offset: 클릭 위치 오프셋 (x, y)

        Returns:
            성공 여부
        """
        frame = self.ctrl.get_frame(agent_name)
        if frame is None:
            print(f"[ERROR] 프레임 없음: {agent_name}")
            return False

        match = self.finder.find_template(frame, template_path, threshold)
        if match is None:
            print(f"[WARN] 템플릿 찾기 실패: {template_path}")
            return False

        # 중심 좌표 + 오프셋
        cx, cy = self.finder.get_center(match)
        cx += offset[0]
        cy += offset[1]

        print(f"[OK] 템플릿 발견: {template_path} at ({cx}, {cy})")
        return self.ctrl.send_click(agent_name, cx, cy, human_like=human_like)

    def double_click_template(self, agent_name: str, template_path: str,
                              threshold: float = 0.8, human_like: bool = True) -> bool:
        """템플릿을 찾아서 더블클릭"""
        frame = self.ctrl.get_frame(agent_name)
        if frame is None:
            return False

        match = self.finder.find_template(frame, template_path, threshold)
        if match is None:
            return False

        cx, cy = self.finder.get_center(match)
        return self.ctrl.send_double_click(agent_name, cx, cy, human_like=human_like)

    def wait_and_click(self, agent_name: str, template_path: str,
                       timeout: float = 10, interval: float = 0.5,
                       threshold: float = 0.8, human_like: bool = True) -> bool:
        """
        템플릿이 나타날 때까지 대기 후 클릭

        Args:
            agent_name: Agent 이름
            template_path: 템플릿 이미지 경로
            timeout: 타임아웃 (초)
            interval: 확인 간격 (초)
            threshold: 매칭 임계값
            human_like: 사람처럼 동작

        Returns:
            성공 여부
        """
        import time
        start = time.time()

        while time.time() - start < timeout:
            if self.click_template(agent_name, template_path, threshold, human_like):
                return True
            time.sleep(interval)

        print(f"[WARN] 타임아웃: {template_path} ({timeout}초)")
        return False

    def wait_for_template(self, agent_name: str, template_path: str,
                          timeout: float = 10, interval: float = 0.5,
                          threshold: float = 0.8) -> Optional[Tuple]:
        """
        템플릿이 나타날 때까지 대기

        Returns:
            매칭 결과 또는 None
        """
        import time
        start = time.time()

        while time.time() - start < timeout:
            frame = self.ctrl.get_frame(agent_name)
            if frame is not None:
                match = self.finder.find_template(frame, template_path, threshold)
                if match:
                    return match
            time.sleep(interval)

        return None

    def drag_template_to_template(self, agent_name: str,
                                  from_template: str, to_template: str,
                                  threshold: float = 0.8, human_like: bool = True) -> bool:
        """템플릿에서 템플릿으로 드래그"""
        frame = self.ctrl.get_frame(agent_name)
        if frame is None:
            return False

        from_match = self.finder.find_template(frame, from_template, threshold)
        to_match = self.finder.find_template(frame, to_template, threshold)

        if from_match is None or to_match is None:
            return False

        from_x, from_y = self.finder.get_center(from_match)
        to_x, to_y = self.finder.get_center(to_match)

        return self.ctrl.send_drag(agent_name, from_x, from_y, to_x, to_y, human_like)


# ── 테스트 ──

if __name__ == "__main__":
    import cv2

    # 테스트 이미지로 확인
    print("=== TargetFinder 테스트 ===")

    finder = TargetFinder()

    # 테스트용 이미지 생성
    test_frame = np.zeros((600, 800, 3), dtype=np.uint8)
    cv2.rectangle(test_frame, (100, 100), (200, 150), (0, 255, 0), -1)
    cv2.putText(test_frame, "Button", (110, 130), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 2)

    # 템플릿으로 저장
    template_path = finder.capture_template(test_frame, 100, 100, 100, 50, "test_button")

    # 매칭 테스트
    match = finder.find_template(test_frame, "test_button.png", threshold=0.9)
    if match:
        print(f"매칭 성공: {match}")
        cx, cy = finder.get_center(match)
        print(f"중심 좌표: ({cx}, {cy})")

        # 결과 시각화
        x, y, w, h, conf = match
        cv2.rectangle(test_frame, (x, y), (x+w, y+h), (0, 0, 255), 2)
        cv2.circle(test_frame, (cx, cy), 5, (255, 0, 0), -1)

        cv2.imshow("Test", test_frame)
        cv2.waitKey(0)
        cv2.destroyAllWindows()
    else:
        print("매칭 실패!")
