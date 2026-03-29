"""
Remote Agent - 대상 PC에서 실행
화면 캡처 + WebSocket 스트리밍 + Leonardo HID 제어

사용법:
    # 전체 화면 캡처
    python agent.py --port 8765 --leonardo COM6

    # 대화형 창 선택 (최대 3개)
    python agent.py --port 8765 --leonardo COM6 --select

    # 특정 창 지정 (여러 개 가능)
    python agent.py --leonardo COM6 --window "프로그램1" --window "프로그램2"

    # 열린 창 목록 확인
    python agent.py --list-windows
"""

import asyncio
import json
import base64
import time
import argparse
from io import BytesIO
from typing import Optional, Callable, List, Dict
import threading

import numpy as np
import mss
import win32gui
import win32con
import win32ui
import ctypes
from ctypes import windll
from PIL import Image

from leonardo_controller import LeonardoHID

# DPI 인식 설정 (물리 픽셀 좌표 사용 - Win32 API와 캡처 이미지 좌표 일치시킴)
try:
    windll.shcore.SetProcessDpiAwareness(2)  # PROCESS_PER_MONITOR_DPI_AWARE
except Exception:
    try:
        windll.user32.SetProcessDPIAware()
    except Exception:
        pass

try:
    import websockets
except ImportError:
    print("websockets 설치 필요: pip install websockets")
    exit(1)

MAX_WINDOWS = 4  # 최대 동시 캡처 창 수 (Gersang 3 + GersangStation Mini 1)


def auto_detect_leonardo_port() -> Optional[str]:
    """Leonardo COM 포트 자동 감지 (유일한 시리얼 포트 사용)"""
    try:
        import serial.tools.list_ports
        ports = list(serial.tools.list_ports.comports())
        if len(ports) == 1:
            print(f"[OK] COM 포트 자동 감지: {ports[0].device} ({ports[0].description})")
            return ports[0].device
        elif len(ports) > 1:
            # Arduino Leonardo VID/PID로 찾기
            for p in ports:
                if p.vid == 0x2341 or p.vid == 0x045E:  # Arduino / Microsoft(위장)
                    print(f"[OK] Leonardo 포트 감지: {p.device} ({p.description})")
                    return p.device
            print(f"[WARN] 시리얼 포트 {len(ports)}개 발견, 첫 번째 사용: {ports[0].device}")
            return ports[0].device
        else:
            print("[WARN] 시리얼 포트 없음")
            return None
    except Exception as e:
        print(f"[ERROR] 포트 감지 실패: {e}")
        return None


def find_gersang_windows() -> List[str]:
    """Gersang + GersangStation Mini 창 자동 검색 (순서: Gersang들 먼저, Station 마지막)"""
    all_wins = WindowCapture.list_windows()
    gersang = [w["title"] for w in all_wins if w["title"] == "Gersang"]
    station = [w["title"] for w in all_wins if w["title"] == "GersangStation Mini"]
    result = gersang[:3] + station[:1]  # Gersang 최대 3개 + Station 1개
    return result


class WindowCapture:
    """특정 창 캡처 클래스 - 최소화/가려진 창도 캡처 가능"""

    def __init__(self, window_title: Optional[str] = None, use_printwindow: bool = True):
        self.hwnd = None
        self.window_title = window_title
        self.sct = mss.mss()
        self.use_printwindow = use_printwindow  # True: 최소화 창도 캡처 가능

        if window_title:
            self.find_window(window_title)

    def find_window(self, title: str) -> bool:
        """창 제목으로 윈도우 핸들 찾기 (정확한 일치 우선)"""
        self.window_title = title
        self.hwnd = None

        def enum_callback(hwnd, results):
            if win32gui.IsWindowVisible(hwnd):
                window_text = win32gui.GetWindowText(hwnd)
                if window_text:
                    results.append((hwnd, window_text))
            return True

        all_windows = []
        win32gui.EnumWindows(enum_callback, all_windows)

        # 1. 정확히 일치하는 창 찾기
        for hwnd, window_text in all_windows:
            if window_text.lower() == title.lower():
                self.hwnd = hwnd
                print(f"[OK] 창 찾음 (정확 일치): {window_text}")
                return True

        # 2. 부분 일치하는 창 찾기 (짧은 제목 우선 = 더 정확한 매칭)
        matches = []
        for hwnd, window_text in all_windows:
            if title.lower() in window_text.lower():
                matches.append((hwnd, window_text, len(window_text)))

        if matches:
            # 제목이 짧은 순으로 정렬 (더 정확한 매칭)
            matches.sort(key=lambda x: x[2])
            self.hwnd = matches[0][0]
            print(f"[OK] 창 찾음: {matches[0][1]}")
            return True

        print(f"[WARN] 창을 찾을 수 없음: {title}")
        return False

    def get_window_rect(self) -> Optional[tuple]:
        """창 위치와 크기 반환 (x, y, width, height)"""
        if not self.hwnd:
            return None

        try:
            rect = win32gui.GetWindowRect(self.hwnd)
            x, y, right, bottom = rect
            return (x, y, right - x, bottom - y)
        except:
            return None

    def capture_printwindow(self, quality: int = 70) -> Optional[bytes]:
        """
        PrintWindow API로 창 캡처 (최소화/가려진 창도 가능)
        """
        if not self.hwnd:
            return None

        try:
            # 창 크기 가져오기 (최소화된 경우 복원 크기 사용)
            placement = win32gui.GetWindowPlacement(self.hwnd)
            if placement[1] == win32con.SW_SHOWMINIMIZED:
                # 최소화된 상태 - 복원 시 크기 사용
                rect = placement[4]  # (left, top, right, bottom)
                w = rect[2] - rect[0]
                h = rect[3] - rect[1]
            else:
                rect = win32gui.GetClientRect(self.hwnd)
                w = rect[2] - rect[0]
                h = rect[3] - rect[1]

            if w <= 0 or h <= 0:
                return None

            # 디바이스 컨텍스트 생성 (클라이언트 영역 DC)
            hwnd_dc = win32gui.GetDC(self.hwnd)
            mfc_dc = win32ui.CreateDCFromHandle(hwnd_dc)
            save_dc = mfc_dc.CreateCompatibleDC()

            # 비트맵 생성
            bitmap = win32ui.CreateBitmap()
            bitmap.CreateCompatibleBitmap(mfc_dc, w, h)
            save_dc.SelectObject(bitmap)

            # PrintWindow로 캡처 (PW_CLIENTONLY | PW_RENDERFULLCONTENT = 3)
            result = windll.user32.PrintWindow(self.hwnd, save_dc.GetSafeHdc(), 3)

            if result == 0:
                # PrintWindow 실패 시 BitBlt 시도
                save_dc.BitBlt((0, 0), (w, h), mfc_dc, (0, 0), win32con.SRCCOPY)

            # 비트맵 → PIL Image
            bmp_info = bitmap.GetInfo()
            bmp_bits = bitmap.GetBitmapBits(True)
            img = Image.frombuffer('RGB', (bmp_info['bmWidth'], bmp_info['bmHeight']),
                                   bmp_bits, 'raw', 'BGRX', 0, 1)

            # 정리
            win32gui.DeleteObject(bitmap.GetHandle())
            save_dc.DeleteDC()
            mfc_dc.DeleteDC()
            win32gui.ReleaseDC(self.hwnd, hwnd_dc)

            # JPEG 압축
            buffer = BytesIO()
            img.save(buffer, format="JPEG", quality=quality)
            return buffer.getvalue()

        except Exception as e:
            print(f"[ERROR] PrintWindow 캡처 실패: {e}")
            return None

    def capture_mss(self, quality: int = 70) -> Optional[bytes]:
        """
        mss로 창 캡처 (빠르지만 가려진 창은 캡처 불가)
        """
        rect = self.get_client_rect() if self.hwnd else None
        if not rect:
            # 창이 없으면 전체 화면 캡처
            monitor = self.sct.monitors[1]
            rect = (monitor["left"], monitor["top"], monitor["width"], monitor["height"])

        x, y, w, h = rect

        # mss로 캡처
        monitor = {"left": x, "top": y, "width": w, "height": h}
        try:
            screenshot = self.sct.grab(monitor)
            img = Image.frombytes("RGB", screenshot.size, screenshot.bgra, "raw", "BGRX")

            # JPEG으로 압축
            buffer = BytesIO()
            img.save(buffer, format="JPEG", quality=quality)
            return buffer.getvalue()
        except Exception as e:
            print(f"[ERROR] mss 캡처 실패: {e}")
            return None

    def capture(self, quality: int = 70) -> Optional[bytes]:
        """
        창 캡처 후 JPEG 바이트로 반환
        - use_printwindow=True: 최소화/가려진 창도 캡처 가능
        - use_printwindow=False: mss 사용 (빠름)
        """
        if self.hwnd and self.use_printwindow:
            result = self.capture_printwindow(quality)
            if result:
                return result
            # PrintWindow 실패 시 mss로 폴백

        return self.capture_mss(quality)

    def capture_numpy(self) -> Optional[np.ndarray]:
        """창 캡처 후 numpy 배열로 반환 (OpenCV 호환)"""
        if self.hwnd and self.use_printwindow:
            try:
                # PrintWindow 방식
                placement = win32gui.GetWindowPlacement(self.hwnd)
                if placement[1] == win32con.SW_SHOWMINIMIZED:
                    rect = placement[4]
                    w = rect[2] - rect[0]
                    h = rect[3] - rect[1]
                else:
                    rect = win32gui.GetClientRect(self.hwnd)
                    w = rect[2] - rect[0]
                    h = rect[3] - rect[1]

                if w <= 0 or h <= 0:
                    return None

                hwnd_dc = win32gui.GetDC(self.hwnd)
                mfc_dc = win32ui.CreateDCFromHandle(hwnd_dc)
                save_dc = mfc_dc.CreateCompatibleDC()

                bitmap = win32ui.CreateBitmap()
                bitmap.CreateCompatibleBitmap(mfc_dc, w, h)
                save_dc.SelectObject(bitmap)

                windll.user32.PrintWindow(self.hwnd, save_dc.GetSafeHdc(), 3)

                bmp_info = bitmap.GetInfo()
                bmp_bits = bitmap.GetBitmapBits(True)
                img = np.frombuffer(bmp_bits, dtype=np.uint8)
                img = img.reshape((bmp_info['bmHeight'], bmp_info['bmWidth'], 4))

                win32gui.DeleteObject(bitmap.GetHandle())
                save_dc.DeleteDC()
                mfc_dc.DeleteDC()
                win32gui.ReleaseDC(self.hwnd, hwnd_dc)

                return img[:, :, :3]  # BGR

            except Exception as e:
                pass  # mss로 폴백

        # mss 방식 (클라이언트 영역만 캡처)
        rect = self.get_client_rect() if self.hwnd else None
        if not rect:
            monitor = self.sct.monitors[1]
            rect = (monitor["left"], monitor["top"], monitor["width"], monitor["height"])

        x, y, w, h = rect
        monitor = {"left": x, "top": y, "width": w, "height": h}

        try:
            screenshot = self.sct.grab(monitor)
            return np.array(screenshot)[:, :, :3]  # BGR
        except:
            return None

    def bring_to_front(self):
        """창을 앞으로 가져오기 — 타이틀바 좌표 반환 (HID 클릭용)"""
        if not self.hwnd:
            return None

        try:
            placement = win32gui.GetWindowPlacement(self.hwnd)
            if placement[1] == win32con.SW_SHOWMINIMIZED:
                win32gui.ShowWindow(self.hwnd, win32con.SW_RESTORE)
        except:
            pass

        # 타이틀바 중앙 좌표 반환 → 호출자가 HID로 클릭
        try:
            rect = win32gui.GetWindowRect(self.hwnd)
            return ((rect[0] + rect[2]) // 2, rect[1] + 10)
        except:
            return None

    def get_client_rect(self) -> Optional[tuple]:
        """창 클라이언트 영역 위치 (x, y, width, height) - 타이틀바 제외"""
        if not self.hwnd:
            return None
        try:
            # 클라이언트 영역 크기
            client_rect = win32gui.GetClientRect(self.hwnd)
            # 클라이언트 영역의 화면 좌표
            point = win32gui.ClientToScreen(self.hwnd, (0, 0))
            return (point[0], point[1], client_rect[2], client_rect[3])
        except:
            return self.get_window_rect()

    @staticmethod
    def list_windows() -> List[Dict]:
        """현재 열린 모든 창 목록 (상세 정보)"""
        windows = []

        def enum_callback(hwnd, results):
            if win32gui.IsWindowVisible(hwnd):
                title = win32gui.GetWindowText(hwnd)
                if title and len(title.strip()) > 0:
                    try:
                        rect = win32gui.GetWindowRect(hwnd)
                        w = rect[2] - rect[0]
                        h = rect[3] - rect[1]
                        # 너무 작은 창 제외
                        if w > 100 and h > 100:
                            results.append({
                                "hwnd": hwnd,
                                "title": title,
                                "width": w,
                                "height": h
                            })
                    except:
                        pass
            return True

        win32gui.EnumWindows(enum_callback, windows)
        return windows

    @staticmethod
    def select_windows_interactive(max_count: int = 3) -> List[str]:
        """대화형으로 창 선택"""
        windows = WindowCapture.list_windows()

        print("\n=== 창 목록 ===")
        for i, win in enumerate(windows):
            print(f"  [{i+1}] {win['title'][:50]} ({win['width']}x{win['height']})")

        print(f"\n스트리밍할 창 번호를 입력하세요 (최대 {max_count}개, 쉼표로 구분)")
        print("예: 1,3,5 또는 1 (Enter=전체화면)")

        try:
            selection = input("> ").strip()
            if not selection:
                return []  # 전체 화면

            indices = [int(x.strip()) - 1 for x in selection.split(",")]
            selected = []
            for idx in indices[:max_count]:
                if 0 <= idx < len(windows):
                    selected.append(windows[idx]["title"])

            return selected
        except:
            return []


class RemoteAgent:
    """원격 에이전트 - WebSocket 서버 + 다중 창 스트리밍 + HID 제어"""

    def __init__(self, port: int = 8765, leonardo_port: Optional[str] = None,
                 window_titles: Optional[List[str]] = None):
        self.port = port
        self.hid: Optional[LeonardoHID] = None
        self.leonardo_port = leonardo_port

        # 다중 창 캡처 (최대 3개)
        self.captures: Dict[str, WindowCapture] = {}
        self.active_window: str = "screen"  # 현재 활성 창 (HID 명령 대상)

        # 창 설정 (같은 이름 여러 개 지원 — hwnd로 구분)
        if window_titles:
            used_hwnds = set()
            all_wins = WindowCapture.list_windows()
            for i, title in enumerate(window_titles[:MAX_WINDOWS]):
                win_id = f"win{i}"
                cap = WindowCapture(None)
                cap.window_title = title
                # 같은 이름 중 아직 사용 안 한 hwnd 찾기
                for w in all_wins:
                    if w["title"] == title and w["hwnd"] not in used_hwnds:
                        cap.hwnd = w["hwnd"]
                        used_hwnds.add(w["hwnd"])
                        break
                if cap.hwnd:
                    self.captures[win_id] = cap
                    if i == 0:
                        self.active_window = win_id
                    print(f"[OK] 창 {i+1} 추가: {title} (hwnd={cap.hwnd})")
                else:
                    print(f"[WARN] 창 찾을 수 없음: {title}")

        # 창이 없으면 전체 화면
        if not self.captures:
            self.captures["screen"] = WindowCapture(None)
            print("[OK] 전체 화면 캡처 모드")

        # 스트리밍 설정
        self.streaming = False
        self.fps = 15
        self.quality = 85
        self.stream_clients = set()  # 스트리밍 WebSocket
        self.cmd_clients = set()  # 명령 WebSocket

        # 마우스 설정 보정
        self.mouse_speed_factor = self._detect_mouse_speed_factor()
        self._original_mouse_accel = self._disable_mouse_acceleration()

        # Leonardo 연결
        if leonardo_port:
            try:
                self.hid = LeonardoHID(leonardo_port)
                self.hid.release_all()  # 이전 세션에서 눌린 키 해제
                print(f"[OK] Leonardo 연결됨: {leonardo_port}")
            except Exception as e:
                print(f"[WARN] Leonardo 연결 실패: {e}")

    def _disable_mouse_acceleration(self) -> tuple:
        """마우스 가속(Enhance pointer precision) 비활성화 - HID 상대이동 정확도 확보"""
        try:
            # SPI_GETMOUSE = 0x0003: 현재 마우스 가속 설정 읽기
            # [0]=threshold1, [1]=threshold2, [2]=acceleration (0=off, 1=on)
            original = (ctypes.c_int * 3)()
            windll.user32.SystemParametersInfoW(0x0003, 0, ctypes.byref(original), 0)

            if original[2] != 0:
                # SPI_SETMOUSE = 0x0004: 가속 비활성화
                no_accel = (ctypes.c_int * 3)(0, 0, 0)
                windll.user32.SystemParametersInfoW(0x0004, 0, no_accel, 0)
                print(f"[INFO] 마우스 가속(EPP) 비활성화됨 (원래: threshold={original[0]},{original[1]}, accel={original[2]})")
            else:
                print(f"[INFO] 마우스 가속(EPP) 이미 꺼져 있음")

            return (original[0], original[1], original[2])
        except Exception as e:
            print(f"[WARN] 마우스 가속 설정 실패: {e}")
            return (0, 0, 0)

    def _restore_mouse_acceleration(self):
        """종료 시 마우스 가속 설정 복원"""
        try:
            if hasattr(self, '_original_mouse_accel') and self._original_mouse_accel[2] != 0:
                restore = (ctypes.c_int * 3)(*self._original_mouse_accel)
                windll.user32.SystemParametersInfoW(0x0004, 0, restore, 0)
                print(f"[INFO] 마우스 가속 설정 복원됨")
        except Exception:
            pass

    def _detect_mouse_speed_factor(self) -> float:
        """Windows 마우스 포인터 속도 설정에 따른 보정 계수 (mickey → pixel)"""
        try:
            speed = ctypes.c_int(0)
            # SPI_GETMOUSESPEED = 0x0070, 반환값 1~20 (기본 10)
            windll.user32.SystemParametersInfoW(0x0070, 0, ctypes.byref(speed), 0)
            speed_val = speed.value
            # 속도별 mickey→pixel 변환 계수 (속도 10 = 1:1)
            factors = [
                0, 0.03125, 0.0625, 0.125, 0.25, 0.375,
                0.5, 0.625, 0.75, 0.875, 1.0,
                1.25, 1.5, 1.75, 2.0, 2.25,
                2.5, 2.75, 3.0, 3.25, 3.5
            ]
            factor = factors[speed_val] if 1 <= speed_val <= 20 else 1.0
            print(f"[INFO] 마우스 속도: {speed_val}/20, 보정 계수: {factor}")
            if factor != 1.0:
                print(f"[INFO] 마우스 속도가 기본값(10)이 아닙니다. 좌표 보정 적용됨")
            return factor
        except Exception as e:
            print(f"[WARN] 마우스 속도 감지 실패: {e}, 기본값(1.0) 사용")
            return 1.0

    def _pixels_to_mickeys(self, px: int, py: int) -> tuple:
        """화면 픽셀 좌표 → HID mickey 좌표 변환"""
        if self.mouse_speed_factor == 1.0:
            return px, py
        return int(px / self.mouse_speed_factor), int(py / self.mouse_speed_factor)

    async def handle_stream_client(self, websocket):
        """스트리밍 전용 WebSocket (:stream_port)"""
        self.stream_clients.add(websocket)
        client_ip = websocket.remote_address[0]
        print(f"[+] 스트리밍 클라이언트 연결: {client_ip}")

        try:
            # 초기 정보 전송
            await self.send_info(websocket)

            # 스트리밍 루프 (이 WebSocket은 send만 함)
            interval = 1.0 / self.fps
            while True:
                start = time.time()
                frames = await asyncio.to_thread(self._capture_all_frames)
                for frame_msg in frames:
                    await websocket.send(json.dumps(frame_msg))
                elapsed = time.time() - start
                if elapsed < interval:
                    await asyncio.sleep(interval - elapsed)

        except (websockets.exceptions.ConnectionClosed, asyncio.CancelledError):
            pass
        except Exception as e:
            print(f"[ERROR] 스트리밍 오류: {e}")
        finally:
            self.stream_clients.discard(websocket)
            print(f"[-] 스트리밍 클라이언트 종료: {client_ip}")

    async def handle_cmd_client(self, websocket):
        """명령 전용 WebSocket (:cmd_port)"""
        self.cmd_clients.add(websocket)
        client_ip = websocket.remote_address[0]
        print(f"[+] 명령 클라이언트 연결: {client_ip}")

        try:
            # 초기 정보 전송
            await self.send_info(websocket)

            # 명령 수신 루프
            async for message in websocket:
                try:
                    cmd = json.loads(message)
                    await self.handle_command(cmd, websocket)
                except json.JSONDecodeError:
                    await websocket.send(json.dumps({"error": "Invalid JSON"}))

        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            self.cmd_clients.discard(websocket)
            print(f"[-] 명령 클라이언트 종료: {client_ip}")

    async def send_info(self, websocket):
        """에이전트 정보 전송"""
        # 모든 창 정보
        windows_info = {}
        for win_id, cap in self.captures.items():
            rect = cap.get_window_rect()
            windows_info[win_id] = {
                "title": cap.window_title or "전체화면",
                "rect": rect
            }

        info = {
            "type": "info",
            "windows": windows_info,
            "active_window": self.active_window,
            "leonardo": self.hid is not None,
            "fps": self.fps
        }
        await websocket.send(json.dumps(info))

    def _capture_all_frames(self) -> list:
        """모든 창 캡처 (스레드에서 실행 - 블로킹 I/O)"""
        frames = []
        for win_id, cap in self.captures.items():
            frame_data = cap.capture(quality=self.quality)
            if frame_data:
                rect = cap.get_client_rect()
                frames.append({
                    "type": "frame",
                    "window_id": win_id,
                    "window_title": cap.window_title or "전체화면",
                    "data": base64.b64encode(frame_data).decode("utf-8"),
                    "rect": {
                        "x": rect[0] if rect else 0,
                        "y": rect[1] if rect else 0,
                        "w": rect[2] if rect else 1920,
                        "h": rect[3] if rect else 1080
                    },
                    "active": win_id == self.active_window,
                    "timestamp": time.time()
                })
        return frames

    async def handle_command(self, cmd: dict, websocket):
        """명령 처리"""
        cmd_type = cmd.get("type")
        action = cmd.get("action")
        params = cmd.get("params", {})

        response = {"type": "response", "success": False}

        try:
            # 추적 이동+클릭 (응답 없음 — fire-and-forget, human-like 이동)
            if cmd_type == "move_and_click":
                if self.hid:
                    self.hid.cancel_pending()
                    x, y = params.get("x", 0), params.get("y", 0)
                    button = params.get("button", "RIGHT")
                    if self.active_window in self.captures:
                        cap = self.captures[self.active_window]
                        client_rect = cap.get_client_rect()
                        if client_rect:
                            x = client_rect[0] + x
                            y = client_rect[1] + y
                    # 커서 위치 동기화
                    try:
                        cursor = win32gui.GetCursorPos()
                        self.hid._mouse_x = cursor[0]
                        self.hid._mouse_y = cursor[1]
                    except:
                        pass
                    print(f"[TRACK] human-like 이동+클릭: ({self.hid._mouse_x},{self.hid._mouse_y}) → ({x},{y}) btn={button}")
                    # human-like 베지어 곡선 이동
                    await asyncio.to_thread(self.hid.mouse_move_to_human, x, y)
                    # 클릭
                    if button and button != "NONE":
                        await asyncio.to_thread(self.hid.mouse_click_human, button)
                return  # 응답 없음

            # 실시간 마우스 절대 위치 (Arduino HID MOUSE_MOVE)
            if cmd_type == "realtime_mouse_pos":
                if self.hid:
                    self.hid.cancel_pending()  # 진행 중 작업 취소
                    x, y = params.get("x", 0), params.get("y", 0)
                    if self.active_window in self.captures:
                        cap = self.captures[self.active_window]
                        client_rect = cap.get_client_rect()
                        if client_rect:
                            screen_x = client_rect[0] + x
                            screen_y = client_rect[1] + y
                            await asyncio.to_thread(
                                self._realtime_move_to, screen_x, screen_y)
                return

            if cmd_type == "command":
                if not self.hid:
                    response["error"] = "Leonardo not connected"
                else:
                    self.hid.cancel_pending()  # 새 명령 → 기존 human-like 작업 취소
                    result = await asyncio.to_thread(self.execute_hid_command, action, params)
                    response["success"] = result
                    response["action"] = action

            elif cmd_type == "add_window":
                # 창 추가 (최대 3개)
                title = params.get("title")
                if len(self.captures) >= MAX_WINDOWS:
                    response["error"] = f"최대 {MAX_WINDOWS}개 창만 가능"
                else:
                    win_id = f"win{len(self.captures)}"
                    cap = WindowCapture(title)
                    if cap.hwnd:
                        self.captures[win_id] = cap
                        response["success"] = True
                        response["window_id"] = win_id
                        response["title"] = title
                    else:
                        response["error"] = f"창을 찾을 수 없음: {title}"

            elif cmd_type == "remove_window":
                # 창 제거
                win_id = params.get("window_id")
                if win_id in self.captures:
                    del self.captures[win_id]
                    if self.active_window == win_id:
                        self.active_window = list(self.captures.keys())[0] if self.captures else "screen"
                    response["success"] = True
                else:
                    response["error"] = f"창을 찾을 수 없음: {win_id}"

            elif cmd_type == "set_active_window":
                # 활성 창 변경 — HID로 타이틀바 클릭하여 활성화
                win_id = params.get("window_id")
                if win_id in self.captures:
                    self.active_window = win_id
                    titlebar_pos = self.captures[win_id].bring_to_front()
                    if titlebar_pos and self.hid:
                        # HID로 타이틀바 클릭 (하드웨어 입력 → xgincode 안전)
                        await asyncio.to_thread(
                            self._hid_click_at, titlebar_pos[0], titlebar_pos[1])
                    # 마우스 위치 추적 무효화
                    if self.hid:
                        self.hid._mouse_x = None
                        self.hid._mouse_y = None
                    response["success"] = True
                    response["active_window"] = win_id
                else:
                    response["error"] = f"창을 찾을 수 없음: {win_id}"

            elif cmd_type == "set_fps":
                self.fps = params.get("fps", 15)
                response["success"] = True
                response["fps"] = self.fps

            elif cmd_type == "set_quality":
                self.quality = params.get("quality", 70)
                response["success"] = True
                response["quality"] = self.quality

            elif cmd_type == "find_next_gersang":
                # 아직 스트리밍하지 않는 Gersang 창 찾아서 추가
                all_wins = WindowCapture.list_windows()
                # 이미 캡처 중인 hwnd 목록
                captured_hwnds = {cap.hwnd for cap in self.captures.values() if cap.hwnd}
                found = False
                for w in all_wins:
                    if w["title"] == "Gersang" and w["hwnd"] not in captured_hwnds:
                        if len(self.captures) >= MAX_WINDOWS:
                            response["error"] = f"최대 {MAX_WINDOWS}개 창"
                            break
                        win_id = f"win{len(self.captures)}"
                        cap = WindowCapture("Gersang")
                        # 정확한 hwnd 직접 지정
                        cap.hwnd = w["hwnd"]
                        cap.window_title = "Gersang"
                        self.captures[win_id] = cap
                        response["success"] = True
                        response["window_id"] = win_id
                        response["title"] = "Gersang"
                        found = True
                        print(f"[OK] 새 Gersang 창 추가: {win_id} (hwnd={w['hwnd']})")
                        break
                if not found and "error" not in response:
                    response["error"] = "추가할 Gersang 창 없음"

            elif cmd_type == "list_windows":
                # 시스템의 모든 창 목록
                windows = WindowCapture.list_windows()
                response["success"] = True
                response["windows"] = [w["title"] for w in windows]

            elif cmd_type == "get_streams":
                # 현재 스트리밍 중인 창 목록
                streams = {}
                for win_id, cap in self.captures.items():
                    streams[win_id] = {
                        "title": cap.window_title or "전체화면",
                        "rect": cap.get_window_rect(),
                        "active": win_id == self.active_window
                    }
                response["success"] = True
                response["streams"] = streams

            elif cmd_type == "ping":
                response["success"] = True
                response["pong"] = time.time()

        except Exception as e:
            response["error"] = str(e)

        await websocket.send(json.dumps(response))

    def _hid_click_at(self, screen_x: int, screen_y: int):
        """HID로 특정 화면 좌표 클릭 (타이틀바 활성화용)"""
        try:
            # 현재 커서 위치 저장
            saved_x, saved_y = self.hid._mouse_x, self.hid._mouse_y

            # 현재 커서 위치 초기화
            cursor = win32gui.GetCursorPos()
            self.hid._mouse_x = cursor[0]
            self.hid._mouse_y = cursor[1]

            # 타이틀바로 이동 + 클릭
            mx, my = self._pixels_to_mickeys(screen_x, screen_y)
            self.hid.mouse_move_to(mx, my)
            import time
            time.sleep(0.05)
            self.hid.mouse_click("LEFT")
            time.sleep(0.1)

            # 위치 추적 무효화 (다음 명령에서 GetCursorPos로 재초기화)
            self.hid._mouse_x = None
            self.hid._mouse_y = None
        except Exception as e:
            print(f"[WARN] HID 창 활성화 실패: {e}")

    def _realtime_move_to(self, screen_x: int, screen_y: int):
        """실시간 마우스 이동 — 항상 GetCursorPos로 현재 위치 확인 후 이동"""
        try:
            # 매번 실제 커서 위치에서 delta 계산 (누적 오차 방지)
            cursor = win32gui.GetCursorPos()
            dx = screen_x - cursor[0]
            dy = screen_y - cursor[1]
            if dx != 0 or dy != 0:
                mx, my = self._pixels_to_mickeys(dx, dy)
                self.hid._send(f"MOUSE_MOVE:{mx},{my}")
            # 추적 위치도 동기화
            self.hid._mouse_x = screen_x
            self.hid._mouse_y = screen_y
        except Exception:
            pass

    def execute_hid_command(self, action: str, params: dict) -> bool:
        """HID 명령 실행"""
        if not self.hid:
            return False

        try:
            human_like = params.get("human_like", True)

            if action == "mouse_move":
                x, y = params["x"], params["y"]

                # 창 내부 좌표 → 화면 절대 좌표 변환
                if self.active_window in self.captures:
                    cap = self.captures[self.active_window]
                    client_rect = cap.get_client_rect()
                    if client_rect:
                        win_x, win_y, win_w, win_h = client_rect
                        x = win_x + x
                        y = win_y + y

                # 항상 실제 커서 위치로 동기화 (누적 오차 방지)
                try:
                    cursor = win32gui.GetCursorPos()
                    self.hid._mouse_x = cursor[0]
                    self.hid._mouse_y = cursor[1]
                except:
                    pass

                # delta 계산 후 mickey 보정하여 이동
                cur_x = self.hid._mouse_x or x
                cur_y = self.hid._mouse_y or y
                dx = x - cur_x
                dy = y - cur_y
                print(f"[DEBUG] 이동: ({cur_x},{cur_y}) → ({x},{y}) delta=({dx},{dy})")

                if dx != 0 or dy != 0:
                    mdx, mdy = self._pixels_to_mickeys(dx, dy)
                    if human_like:
                        self.hid.mouse_move_to_human(x, y)
                    else:
                        self.hid._send(f"MOUSE_MOVE:{mdx},{mdy}")
                self.hid._mouse_x = x
                self.hid._mouse_y = y

            elif action == "mouse_click":
                button = params.get("button", "LEFT")
                print(f"[DEBUG] 마우스 클릭: {button}")
                if human_like:
                    self.hid.mouse_click_human(button)
                else:
                    self.hid.mouse_click(button)

            elif action == "mouse_double_click":
                button = params.get("button", "LEFT")
                if human_like:
                    self.hid.mouse_double_click_human(button)
                else:
                    self.hid.mouse_double_click(button)

            elif action == "mouse_drag":
                from_x, from_y = params["from_x"], params["from_y"]
                to_x, to_y = params["to_x"], params["to_y"]
                # 마우스 속도 보정
                from_x, from_y = self._pixels_to_mickeys(from_x, from_y)
                to_x, to_y = self._pixels_to_mickeys(to_x, to_y)
                if human_like:
                    self.hid.mouse_drag_human(from_x, from_y, to_x, to_y)
                else:
                    self.hid.mouse_drag(from_x, from_y, to_x, to_y)

            elif action == "key":
                key = params["key"]
                if human_like:
                    self.hid.key_human(key)
                else:
                    self.hid.key(key)

            elif action == "key_down":
                key = params["key"]
                self.hid.key_down(key)

            elif action == "key_up":
                key = params["key"]
                self.hid.key_up(key)

            elif action == "combo":
                keys = params["keys"]
                if human_like:
                    self.hid.combo_human(keys)
                else:
                    self.hid.combo(keys)

            elif action == "type_text":
                text = params["text"]
                if human_like:
                    self.hid.type_text_human(text)
                else:
                    self.hid.type_text(text)

            elif action == "release_all":
                self.hid.release_all()

            elif action == "wait":
                seconds = params.get("seconds", 1)
                self.hid.wait(seconds)

            else:
                print(f"[WARN] 알 수 없는 액션: {action}")
                return False

            return True

        except Exception as e:
            print(f"[ERROR] HID 명령 실패: {e}")
            return False

    async def start(self):
        """에이전트 서버 시작 (듀얼 WebSocket)"""
        cmd_port = self.port + 1  # 명령 포트 = 스트리밍 포트 + 1

        print(f"\n{'='*50}")
        print(f"  Smart Search Agent")
        print(f"{'='*50}")
        print(f"  스트리밍: ws://0.0.0.0:{self.port}")
        print(f"  명령:     ws://0.0.0.0:{cmd_port}")
        print(f"  Leonardo: {'연결됨 (' + self.leonardo_port + ')' if self.hid else '없음'}")
        print(f"  FPS: {self.fps}, 품질: {self.quality}")
        print(f"\n  스트리밍 창 ({len(self.captures)}개):")
        for win_id, cap in self.captures.items():
            title = cap.window_title or "전체화면"
            active = " [활성]" if win_id == self.active_window else ""
            print(f"    - {win_id}: {title[:40]}{active}")
        print(f"{'='*50}")
        print(f"  대기 중... (Ctrl+C로 종료)\n")

        async with websockets.serve(self.handle_stream_client, "0.0.0.0", self.port):
            async with websockets.serve(self.handle_cmd_client, "0.0.0.0", cmd_port):
                await asyncio.Future()

    def run(self):
        """동기 실행"""
        try:
            asyncio.run(self.start())
        except KeyboardInterrupt:
            print("\n[*] 종료...")
        finally:
            self._restore_mouse_acceleration()
            if self.hid:
                self.hid.close()


# ── CLI ──

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Smart Search Remote Agent")
    parser.add_argument("--port", type=int, default=8765, help="WebSocket 포트 (기본: 8765)")
    parser.add_argument("--leonardo", type=str, default=None, help="Leonardo COM 포트 (None=자동감지)")
    parser.add_argument("--window", type=str, action="append", default=None,
                        help="캡처할 창 제목 (여러 개 지정 가능)")
    parser.add_argument("--select", action="store_true", help="대화형 창 선택 모드")
    parser.add_argument("--auto", action="store_true", default=True,
                        help="자동 모드: Gersang + GersangStation Mini 검색 (기본)")
    parser.add_argument("--fps", type=int, default=60, help="스트리밍 FPS (기본: 60)")
    parser.add_argument("--quality", type=int, default=85, help="JPEG 품질 1-100 (기본: 85)")
    parser.add_argument("--list-windows", action="store_true", help="열린 창 목록 표시")

    args = parser.parse_args()

    # 창 목록 표시
    if args.list_windows:
        windows = WindowCapture.list_windows()
        print("\n=== 열린 창 목록 ===")
        for i, win in enumerate(windows):
            print(f"  [{i+1}] {win['title'][:60]} ({win['width']}x{win['height']})")
        exit(0)

    # Leonardo 포트 (자동 감지)
    leo_port = args.leonardo or auto_detect_leonardo_port()

    # 창 선택
    window_titles = None

    if args.window:
        window_titles = args.window[:MAX_WINDOWS]
    elif args.select:
        window_titles = WindowCapture.select_windows_interactive(MAX_WINDOWS)
        if window_titles:
            print(f"\n선택된 창: {len(window_titles)}개")
            for t in window_titles:
                print(f"  - {t[:50]}")
        else:
            print("\n전체 화면 캡처 모드로 시작합니다.")
    else:
        # 자동 모드: Gersang + GersangStation Mini 검색
        window_titles = find_gersang_windows()
        if window_titles:
            print(f"\n[AUTO] Gersang 창 {len(window_titles)}개 발견:")
            for t in window_titles:
                print(f"  - {t}")
        else:
            print("\n[AUTO] Gersang 창 없음 → 전체 화면 캡처")

    # 5초 후 GersangStation.exe 활성화 + 클릭
    def activate_gersang_station():
        time.sleep(5)
        import subprocess
        try:
            hwnd = None
            def enum_cb(h, _):
                nonlocal hwnd
                if win32gui.IsWindowVisible(h):
                    _, pid = ctypes.c_ulong(), ctypes.c_ulong()
                    ctypes.windll.user32.GetWindowThreadProcessId(h, ctypes.byref(pid))
                    try:
                        import psutil
                        proc = psutil.Process(pid.value)
                        if proc.name().lower() == "gersangstation.exe":
                            hwnd = h
                    except:
                        pass
                return True
            win32gui.EnumWindows(enum_cb, None)
            if hwnd:
                print(f"[STARTUP] GersangStation 창 발견: hwnd={hwnd}")
                win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
                win32gui.SetForegroundWindow(hwnd)
                time.sleep(0.5)
                # 창 기준 (350, 130) 좌클릭
                rect = win32gui.GetWindowRect(hwnd)
                abs_x = rect[0] + 350
                abs_y = rect[1] + 130
                ctypes.windll.user32.SetCursorPos(abs_x, abs_y)
                time.sleep(0.3)
                ctypes.windll.user32.mouse_event(0x0002, 0, 0, 0, 0)  # LEFTDOWN
                time.sleep(0.05)
                ctypes.windll.user32.mouse_event(0x0004, 0, 0, 0, 0)  # LEFTUP
                print(f"[STARTUP] GersangStation ({abs_x},{abs_y}) 좌클릭 완료")
            else:
                print("[STARTUP] GersangStation.exe 창을 찾을 수 없음")
        except Exception as e:
            print(f"[STARTUP] GersangStation 활성화 오류: {e}")

    threading.Thread(target=activate_gersang_station, daemon=True).start()

    # 에이전트 생성 및 실행
    agent = RemoteAgent(
        port=args.port,
        leonardo_port=leo_port,
        window_titles=window_titles
    )
    agent.fps = args.fps
    agent.quality = args.quality
    agent.run()
