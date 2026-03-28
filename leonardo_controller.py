"""
Leonardo HID Macro Controller - Python 제어 모듈
Arduino Leonardo에 시리얼로 명령을 보내 실제 키보드/마우스 입력을 생성

사용법:
    pip install pyserial

    from leonardo_controller import LeonardoHID

    hid = LeonardoHID("COM3")  # 포트 확인 후 지정
    hid.key("a")
    hid.combo(["KEY_LEFT_CTRL", "c"])
    hid.type_text("Hello World")
    hid.mouse_click()
    hid.close()
"""

import serial
import time
import random
import math
import threading
from typing import List, Optional


class LeonardoHID:
    def __init__(self, port: str, baudrate: int = 115200, timeout: float = 2.0):
        """
        Args:
            port: 시리얼 포트 (예: "COM3", "/dev/ttyACM0")
            baudrate: 보드레이트 (기본 9600)
            timeout: 응답 대기 시간
        """
        self.ser = serial.Serial(port, baudrate, timeout=timeout)
        self._lock = threading.Lock()
        self._cancel = threading.Event()  # 진행 중 작업 취소 플래그
        time.sleep(2)  # Leonardo 리셋 대기

        # 마우스 위치 추적 (None = 위치 불명 → 첫 이동 시 원점 리셋)
        self._mouse_x: int = None
        self._mouse_y: int = None

        # READY 메시지 확인
        ready = self._read_response()
        if "READY" not in ready:
            print(f"[WARN] Expected READY, got: {ready}")
        print(f"[OK] Leonardo 연결됨 @ {port}")

    def cancel_pending(self):
        """진행 중인 human-like 작업 취소"""
        self._cancel.set()

    def _send(self, cmd: str) -> str:
        """명령 전송 후 응답 수신 (스레드 안전)"""
        with self._lock:
            self.ser.write(f"{cmd}\n".encode("utf-8"))
            return self._read_response()

    def _read_response(self) -> str:
        """시리얼 응답 읽기"""
        line = self.ser.readline().decode("utf-8").strip()
        return line

    def ping(self) -> bool:
        """연결 상태 확인"""
        resp = self._send("PING")
        return resp == "PONG"

    # ── 키보드 ──

    def key(self, k: str, delay_after: float = 0.05):
        """
        단일 키 입력 (press + release)
        Args:
            k: 키 문자 또는 특수키 이름
               문자: "a", "A", "1", "/"
               특수키: "KEY_RETURN", "KEY_TAB", "KEY_ESC" 등
        """
        if k.startswith("KEY_"):
            self._send(f"SPECIAL:{k}")
        else:
            self._send(f"KEY:{k}")
        time.sleep(delay_after)

    def key_down(self, k: str):
        """키 누르기 (유지)"""
        self._send(f"KEYDOWN:{k}")

    def key_up(self, k: str):
        """키 떼기"""
        self._send(f"KEYUP:{k}")

    def combo(self, keys: List[str], delay_after: float = 0.05):
        """
        조합키 입력
        Args:
            keys: 키 리스트 (예: ["KEY_LEFT_CTRL", "c"])
        """
        combo_str = "+".join(keys)
        self._send(f"COMBO:{combo_str}")
        time.sleep(delay_after)

    def type_text(self, text: str, delay_after: float = 0.05):
        """
        문자열 타이핑 (영문/숫자/기호)
        주의: 한글은 지원 안 됨 (USB HID 한계)
        """
        self._send(f"TYPE:{text}")
        time.sleep(delay_after)

    def enter(self):
        self.key("KEY_RETURN")

    def tab(self):
        self.key("KEY_TAB")

    def esc(self):
        self.key("KEY_ESC")

    def backspace(self, count: int = 1):
        for _ in range(count):
            self.key("KEY_BACKSPACE")

    # ── 마우스 ──

    def mouse_move(self, dx: int, dy: int):
        """마우스 상대 이동 (현재 위치 기준)"""
        self._send(f"MOUSE_MOVE:{dx},{dy}")
        if self._mouse_x is not None:
            self._mouse_x += dx
            self._mouse_y += dy

    def mouse_move_to(self, x: int, y: int, screen_w: int = 1920, screen_h: int = 1080):
        """마우스 절대 좌표 이동 - 이전 위치에서 상대 이동"""
        if self._mouse_x is None:
            # 위치 불명이면 현재 위치로 간주 (agent에서 OS 커서로 초기화됨)
            self._mouse_x = x
            self._mouse_y = y
        else:
            # 현재 위치에서 상대 이동
            dx = x - self._mouse_x
            dy = y - self._mouse_y
            if dx != 0 or dy != 0:
                self._send(f"MOUSE_MOVE:{dx},{dy}")
        self._mouse_x = x
        self._mouse_y = y

    def mouse_reset_position(self, screen_w: int = 1920, screen_h: int = 1080):
        """마우스 위치 추적 리셋 (원점으로 강제 이동)"""
        self._send(f"MOUSE_ABS:0,0,{screen_w},{screen_h}")
        self._mouse_x = 0
        self._mouse_y = 0

    def mouse_click(self, button: str = "LEFT"):
        """마우스 클릭 (MOUSE_DOWN + 유지 + MOUSE_UP)"""
        self._send(f"MOUSE_DOWN:{button}")
        time.sleep(0.05)
        self._send(f"MOUSE_UP:{button}")

    def mouse_double_click(self, button: str = "LEFT"):
        """더블 클릭"""
        self.mouse_click(button)
        time.sleep(0.08)
        self.mouse_click(button)

    def mouse_down(self, button: str = "LEFT"):
        """마우스 버튼 누르기 (드래그 시작)"""
        self._send(f"MOUSE_DOWN:{button}")

    def mouse_up(self, button: str = "LEFT"):
        """마우스 버튼 떼기 (드래그 끝)"""
        self._send(f"MOUSE_UP:{button}")

    def mouse_drag(self, from_x: int, from_y: int, to_x: int, to_y: int,
                   screen_w: int = 1920, screen_h: int = 1080):
        """드래그 동작"""
        self.mouse_move_to(from_x, from_y, screen_w, screen_h)
        time.sleep(0.1)
        self.mouse_down()
        time.sleep(0.1)
        self.mouse_move_to(to_x, to_y, screen_w, screen_h)
        time.sleep(0.1)
        self.mouse_up()

    def mouse_scroll(self, amount: int):
        """스크롤 (양수=위, 음수=아래)"""
        self._send(f"MOUSE_SCROLL:{amount}")

    # ── 유틸 ──

    def delay(self, ms: int):
        """Leonardo 측 딜레이 (시리얼 블로킹)"""
        self._send(f"DELAY:{ms}")

    def wait(self, seconds: float):
        """Python 측 딜레이"""
        time.sleep(seconds)

    def release_all(self):
        """모든 키/마우스 해제"""
        self._send("RELEASE_ALL")

    # ── 사람처럼 자연스러운 입력 (패턴 없는 랜덤) ──

    def _get_random_profile(self):
        """매번 다른 타이핑 프로파일 생성 (패턴 방지)"""
        return {
            'base_delay': random.uniform(0.04, 0.12),
            'variance': random.uniform(0.02, 0.08),
            'pause_chance': random.uniform(0.03, 0.12),
            'pause_duration': (random.uniform(0.15, 0.4), random.uniform(0.4, 0.8)),
            'burst_chance': random.uniform(0.05, 0.15),  # 빠르게 연타할 확률
            'burst_speed': random.uniform(0.02, 0.05),
        }

    def _typing_delay(self, profile: dict, prev_char: str = "", curr_char: str = ""):
        """컨텍스트 기반 타이핑 딜레이 (같은 손가락, 단어 끝 등 고려)"""
        base = profile['base_delay']
        variance = profile['variance']

        # 기본 랜덤 딜레이
        delay = random.gauss(base, variance)
        delay = max(0.02, delay)  # 최소 20ms

        # 스페이스/특수문자 후에는 약간 더 길게 (단어 구분)
        if prev_char in " .,!?;:":
            delay += random.uniform(0.05, 0.15)

        # 같은 문자 연속 입력시 약간 빠르게
        if prev_char == curr_char:
            delay *= random.uniform(0.6, 0.8)

        # 랜덤 멈춤 (생각하는 척)
        if random.random() < profile['pause_chance']:
            delay += random.uniform(*profile['pause_duration'])

        # 랜덤 버스트 (빠르게 연타)
        if random.random() < profile['burst_chance']:
            delay = profile['burst_speed']

        time.sleep(delay)

    def type_text_human(self, text: str):
        """
        사람처럼 한 글자씩 타이핑 (매번 다른 패턴, 취소 가능)
        """
        if not text:
            return

        self._cancel.clear()
        profile = self._get_random_profile()
        prev_char = ""

        for i, char in enumerate(text):
            if self._cancel.is_set():
                return
            self._send(f"KEY:{char}")
            self._typing_delay(profile, prev_char, char)
            prev_char = char

            if random.random() < 0.1:
                profile['base_delay'] += random.uniform(-0.02, 0.02)
                profile['base_delay'] = max(0.03, min(0.15, profile['base_delay']))

    def key_human(self, k: str):
        """사람처럼 키 입력 (불규칙 딜레이)"""
        # 키 누르기 전 미세 대기 (랜덤)
        if random.random() < 0.3:
            time.sleep(random.uniform(0.02, 0.08))

        if k.startswith("KEY_"):
            self._send(f"SPECIAL:{k}")
        else:
            self._send(f"KEY:{k}")

        # 키 입력 후 랜덤 딜레이 (가우시안 분포로 더 자연스럽게)
        delay = abs(random.gauss(0.15, 0.08))
        delay = max(0.05, min(0.4, delay))
        time.sleep(delay)

    def combo_human(self, keys: List[str]):
        """사람처럼 조합키 입력 (불규칙 딜레이)"""
        # 조합키 전 미세 대기
        if random.random() < 0.4:
            time.sleep(random.uniform(0.03, 0.1))

        combo_str = "+".join(keys)
        self._send(f"COMBO:{combo_str}")

        # 조합키 후 랜덤 딜레이
        delay = abs(random.gauss(0.2, 0.1))
        delay = max(0.08, min(0.5, delay))
        time.sleep(delay)

    def _get_mouse_profile(self):
        """매번 다른 마우스 이동 프로파일"""
        return {
            'speed_factor': random.uniform(0.7, 1.3),
            'curve_intensity': random.uniform(0.2, 0.8),
            'jitter': random.uniform(1, 4),
            'pause_chance': random.uniform(0.02, 0.08),
            'overshoot_chance': random.uniform(0.05, 0.15),
        }

    def mouse_move_to_human(self, x: int, y: int, screen_w: int = 1920, screen_h: int = 1080):
        """
        사람처럼 마우스 이동 (현재 위치 → 목표 위치, 베지어 곡선)
        _cancel 이벤트로 중도 취소 가능
        """
        self._cancel.clear()
        profile = self._get_mouse_profile()

        if random.random() < 0.3:
            time.sleep(random.uniform(0.02, 0.1))

        if self._mouse_x is None:
            self._mouse_x = x
            self._mouse_y = y

        rel_x = x - self._mouse_x
        rel_y = y - self._mouse_y

        distance = math.sqrt(rel_x**2 + rel_y**2)
        if distance < 3:
            if rel_x != 0 or rel_y != 0:
                self._send(f"MOUSE_MOVE:{rel_x},{rel_y}")
            self._mouse_x = x
            self._mouse_y = y
            return

        base_steps = int(distance / 30)
        steps = max(10, min(40, base_steps + random.randint(-5, 10)))

        duration = (distance / 1500) * profile['speed_factor']
        duration = max(0.2, min(1.2, duration))

        intensity = profile['curve_intensity']
        ctrl1_x = int(rel_x * random.uniform(0.2, 0.5) + random.uniform(-50, 50) * intensity)
        ctrl1_y = int(rel_y * random.uniform(0.1, 0.4) + random.uniform(-50, 50) * intensity)
        ctrl2_x = int(rel_x * random.uniform(0.5, 0.8) + random.uniform(-30, 30) * intensity)
        ctrl2_y = int(rel_y * random.uniform(0.6, 0.9) + random.uniform(-30, 30) * intensity)

        prev_px, prev_py = 0, 0

        for i in range(1, steps + 1):
            if self._cancel.is_set():
                # 취소됨 — 현재까지 이동한 위치를 추적에 반영
                self._mouse_x += prev_px
                self._mouse_y += prev_py
                return

            t = i / steps
            t = t * t * (3 - 2 * t)

            mt = 1 - t
            px = int(mt**3 * 0 + 3*mt**2*t * ctrl1_x + 3*mt*t**2 * ctrl2_x + t**3 * rel_x)
            py = int(mt**3 * 0 + 3*mt**2*t * ctrl1_y + 3*mt*t**2 * ctrl2_y + t**3 * rel_y)

            if i < steps - 2:
                jitter = profile['jitter']
                px += int(random.gauss(0, jitter))
                py += int(random.gauss(0, jitter))

            dx = px - prev_px
            dy = py - prev_py
            prev_px, prev_py = px, py

            if dx != 0 or dy != 0:
                self._send(f"MOUSE_MOVE:{dx},{dy}")

            step_delay = (duration / steps) * random.uniform(0.5, 1.8)
            if random.random() < profile['pause_chance']:
                step_delay += random.uniform(0.05, 0.15)
            time.sleep(step_delay)

        if not self._cancel.is_set() and random.random() < profile['overshoot_chance']:
            overshoot_x = random.randint(3, 10) * random.choice([-1, 1])
            overshoot_y = random.randint(3, 10) * random.choice([-1, 1])
            self._send(f"MOUSE_MOVE:{overshoot_x},{overshoot_y}")
            time.sleep(random.uniform(0.05, 0.12))
            self._send(f"MOUSE_MOVE:{-overshoot_x},{-overshoot_y}")
            time.sleep(random.uniform(0.02, 0.06))

        self._mouse_x = x
        self._mouse_y = y

    def mouse_click_human(self, button: str = "LEFT"):
        """사람처럼 클릭 (불규칙한 타이밍)"""
        # 클릭 전 미세 대기 (가우시안)
        pre_delay = abs(random.gauss(0.08, 0.05))
        pre_delay = max(0.02, min(0.2, pre_delay))
        time.sleep(pre_delay)

        # 가끔 클릭 전에 약간 움직임 (손 떨림)
        if random.random() < 0.15:
            jitter_x = random.randint(-2, 2)
            jitter_y = random.randint(-2, 2)
            if jitter_x != 0 or jitter_y != 0:
                self._send(f"MOUSE_MOVE:{jitter_x},{jitter_y}")
                time.sleep(random.uniform(0.01, 0.03))

        self._send(f"MOUSE_DOWN:{button}")
        hold = abs(random.gauss(0.08, 0.03))
        hold = max(0.04, min(0.15, hold))
        time.sleep(hold)
        self._send(f"MOUSE_UP:{button}")

        # 클릭 후 딜레이 (불규칙)
        post_delay = abs(random.gauss(0.12, 0.06))
        post_delay = max(0.03, min(0.3, post_delay))
        time.sleep(post_delay)

    def mouse_double_click_human(self, button: str = "LEFT"):
        """사람처럼 더블클릭 (불규칙 간격)"""
        # 첫 클릭 전 대기
        if random.random() < 0.3:
            time.sleep(random.uniform(0.02, 0.08))

        self._send(f"MOUSE_DOWN:{button}")
        time.sleep(abs(random.gauss(0.06, 0.02)))
        self._send(f"MOUSE_UP:{button}")

        # 더블클릭 간격 (사람마다 다름, 60~180ms 범위)
        interval = abs(random.gauss(0.1, 0.03))
        interval = max(0.05, min(0.18, interval))
        time.sleep(interval)

        self._send(f"MOUSE_DOWN:{button}")
        time.sleep(abs(random.gauss(0.06, 0.02)))
        self._send(f"MOUSE_UP:{button}")

        # 더블클릭 후 대기
        post_delay = abs(random.gauss(0.15, 0.07))
        time.sleep(max(0.05, post_delay))

    def mouse_drag_human(self, from_x: int, from_y: int, to_x: int, to_y: int,
                         screen_w: int = 1920, screen_h: int = 1080):
        """사람처럼 드래그 (불규칙한 속도와 경로)"""
        # 시작점으로 이동
        self.mouse_move_to_human(from_x, from_y, screen_w, screen_h)

        # 드래그 시작 전 대기 (불규칙)
        time.sleep(abs(random.gauss(0.12, 0.05)))

        self.mouse_down()

        # 버튼 누른 후 잠시 대기 (손 안정화)
        time.sleep(abs(random.gauss(0.08, 0.03)))

        # 드래그 중 이동 (상대 이동으로)
        dx = to_x - from_x
        dy = to_y - from_y
        distance = math.sqrt(dx**2 + dy**2)

        # 드래그 단계 수 (불규칙)
        steps = max(8, int(distance / 25) + random.randint(-3, 5))

        profile = self._get_mouse_profile()
        prev_px, prev_py = 0, 0

        for i in range(1, steps + 1):
            t = i / steps
            # ease in-out
            t = t * t * (3 - 2 * t)

            px = int(dx * t)
            py = int(dy * t)

            # 드래그 중 흔들림
            if i < steps - 1:
                px += int(random.gauss(0, profile['jitter']))
                py += int(random.gauss(0, profile['jitter']))

            move_x = px - prev_px
            move_y = py - prev_py
            prev_px, prev_py = px, py

            if move_x != 0 or move_y != 0:
                self._send(f"MOUSE_MOVE:{move_x},{move_y}")

            # 불규칙 딜레이
            step_delay = random.uniform(0.01, 0.04) * profile['speed_factor']
            if random.random() < 0.05:
                step_delay += random.uniform(0.03, 0.08)
            time.sleep(step_delay)

        # 드래그 종료 전 안정화
        time.sleep(abs(random.gauss(0.06, 0.03)))

        self.mouse_up()

        # 드래그 후 대기
        time.sleep(abs(random.gauss(0.1, 0.05)))

    def close(self):
        """연결 종료"""
        self.release_all()
        self.ser.close()
        print("[OK] Leonardo 연결 해제")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


# ── 사용 예시 ──

if __name__ == "__main__":
    import sys

    port = sys.argv[1] if len(sys.argv) > 1 else "COM6"

    with LeonardoHID(port) as hid:
        # 연결 확인
        print(f"Ping: {hid.ping()}")

        # 1. 메모장 열기 (Win+R → notepad) - 사람처럼
        print("--- 메모장 열기 (사람처럼) ---")
        hid.combo_human(["KEY_LEFT_GUI", "r"])
        hid.wait(0.5)
        hid.type_text_human("notepad")  # 한 글자씩 랜덤 속도로
        hid.key_human("KEY_RETURN")
        hid.wait(1.0)

        # 2. 텍스트 입력 - 사람처럼 타이핑
        print("--- 텍스트 입력 (사람처럼) ---")
        hid.type_text_human("Hello from Leonardo HID!")
        hid.key_human("KEY_RETURN")
        hid.type_text_human("This is a human-like typing.")
        hid.key_human("KEY_RETURN")

        # 3. 전체 선택 + 복사 - 사람처럼
        print("--- Ctrl+A, Ctrl+C (사람처럼) ---")
        hid.combo_human(["KEY_LEFT_CTRL", "a"])
        hid.wait(0.3)
        hid.combo_human(["KEY_LEFT_CTRL", "c"])

        # 4. 마우스 이동 + 클릭 - 부드러운 곡선 이동
        print("--- 마우스 동작 (사람처럼) ---")
        hid.mouse_move_to_human(500, 300)  # 자동으로 불규칙한 속도/경로
        hid.mouse_click_human()

        # 5. 드래그 예시 - 사람처럼
        print("--- 드래그 (사람처럼) ---")
        hid.mouse_drag_human(100, 100, 400, 300)

        print("--- 완료 ---")
