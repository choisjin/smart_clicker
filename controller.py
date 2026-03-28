"""
Remote Controller - 관제 PC에서 실행
다중 Agent 연결 관리 + 화면 수신 + 명령 전송

사용법:
    from controller import RemoteController

    ctrl = RemoteController()
    ctrl.connect("192.168.1.100", 8765, "PC1")
    ctrl.send_click("PC1", 500, 300)
"""

import asyncio
import json
import base64
import time
import threading
from typing import Optional, Callable, Dict, List
from io import BytesIO
from dataclasses import dataclass, field
from queue import Queue

import numpy as np
from PIL import Image

try:
    import websockets
except ImportError:
    print("websockets 설치 필요: pip install websockets")
    exit(1)


@dataclass
class WindowFrame:
    """창별 프레임 정보"""
    window_id: str
    title: str
    frame: Optional[np.ndarray] = None
    rect: Optional[dict] = None
    active: bool = False
    timestamp: float = 0


@dataclass
class AgentInfo:
    """Agent 연결 정보"""
    name: str
    host: str
    port: int
    stream_ws: Optional[object] = None  # 스트리밍 전용 WebSocket
    cmd_ws: Optional[object] = None  # 명령 전용 WebSocket
    connected: bool = False
    has_leonardo: bool = False
    fps: int = 15
    active_window: str = "screen"
    # 다중 창 지원
    windows: Dict[str, WindowFrame] = field(default_factory=dict)
    frame_callbacks: List[Callable] = field(default_factory=list)


class RemoteController:
    """원격 컨트롤러 - 여러 Agent 관리"""

    def __init__(self):
        self.agents: Dict[str, AgentInfo] = {}
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._running = False

        # 이벤트 루프 시작
        self._start_event_loop()

    def _start_event_loop(self):
        """백그라운드 이벤트 루프 시작"""
        def run_loop():
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            self._running = True
            self._loop.run_forever()

        self._thread = threading.Thread(target=run_loop, daemon=True)
        self._thread.start()

        # 루프 준비 대기
        while self._loop is None:
            time.sleep(0.01)

    def _run_async(self, coro):
        """비동기 함수를 동기로 실행"""
        if self._loop is None:
            raise RuntimeError("Event loop not running")
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result(timeout=10)

    # ── 연결 관리 ──

    def connect(self, host: str, port: int, name: str) -> bool:
        """Agent에 연결"""
        if name in self.agents:
            print(f"[WARN] 이미 연결된 Agent: {name}")
            return False

        agent = AgentInfo(name=name, host=host, port=port)
        self.agents[name] = agent

        try:
            self._run_async(self._connect_agent(agent))
            return agent.connected
        except Exception as e:
            print(f"[ERROR] 연결 실패 ({name}): {e}")
            del self.agents[name]
            return False

    async def _connect_agent(self, agent: AgentInfo):
        """Agent 연결 (스트리밍 + 명령 듀얼 WebSocket)"""
        stream_uri = f"ws://{agent.host}:{agent.port}"
        cmd_uri = f"ws://{agent.host}:{agent.port + 1}"
        try:
            # 스트리밍 WebSocket
            agent.stream_ws = await websockets.connect(stream_uri)
            # 명령 WebSocket
            agent.cmd_ws = await websockets.connect(cmd_uri)
            agent.connected = True
            print(f"[+] Agent 연결됨: {agent.name} (스트리밍:{stream_uri}, 명령:{cmd_uri})")

            # 스트리밍 소켓에서 초기 정보 수신
            info_msg = await agent.stream_ws.recv()
            info = json.loads(info_msg)
            if info.get("type") == "info":
                agent.has_leonardo = info.get("leonardo", False)
                agent.fps = info.get("fps", 15)
                agent.active_window = info.get("active_window", "screen")
                windows_info = info.get("windows", {})
                for win_id, win_data in windows_info.items():
                    agent.windows[win_id] = WindowFrame(
                        window_id=win_id,
                        title=win_data.get("title", ""),
                        rect=win_data.get("rect")
                    )

            # 명령 소켓의 초기 정보는 버림
            await agent.cmd_ws.recv()

            # 스트리밍 수신 태스크 (프레임만 수신)
            asyncio.create_task(self._stream_receive_loop(agent))

        except Exception as e:
            agent.connected = False
            raise e

    async def _stream_receive_loop(self, agent: AgentInfo):
        """스트리밍 WebSocket에서 프레임만 수신"""
        try:
            async for message in agent.stream_ws:
                try:
                    data = json.loads(message)
                    if data.get("type") != "frame":
                        continue

                    frame_data = base64.b64decode(data["data"])
                    img = Image.open(BytesIO(frame_data))
                    frame_array = np.array(img)

                    window_id = data.get("window_id", "screen")
                    window_title = data.get("window_title", "")
                    rect = data.get("rect")
                    active = data.get("active", False)
                    timestamp = data.get("timestamp", time.time())

                    if window_id not in agent.windows:
                        agent.windows[window_id] = WindowFrame(window_id=window_id, title=window_title)

                    wf = agent.windows[window_id]
                    wf.frame = frame_array
                    wf.title = window_title
                    wf.rect = rect
                    wf.active = active
                    wf.timestamp = timestamp

                    for callback in agent.frame_callbacks:
                        try:
                            callback(agent.name, window_id, frame_array)
                        except Exception as e:
                            print(f"[ERROR] Frame callback: {e}")

                except json.JSONDecodeError:
                    pass

        except websockets.exceptions.ConnectionClosed:
            print(f"[-] Agent 스트리밍 연결 종료: {agent.name}")
            agent.connected = False

    def disconnect(self, name: str):
        """Agent 연결 해제"""
        if name not in self.agents:
            return

        agent = self.agents[name]
        if agent.websocket:
            for ws in [agent.stream_ws, agent.cmd_ws]:
                if ws:
                    try:
                        self._run_async(ws.close())
                    except:
                        pass

        del self.agents[name]
        print(f"[-] Agent 연결 해제: {name}")

    def disconnect_all(self):
        """모든 Agent 연결 해제"""
        for name in list(self.agents.keys()):
            self.disconnect(name)

    def list_agents(self) -> List[dict]:
        """연결된 Agent 목록"""
        return [
            {
                "name": a.name,
                "host": a.host,
                "port": a.port,
                "connected": a.connected,
                "window": a.window,
                "has_leonardo": a.has_leonardo
            }
            for a in self.agents.values()
        ]

    def is_connected(self, name: str) -> bool:
        """Agent 연결 상태 확인"""
        return name in self.agents and self.agents[name].connected

    # ── 명령 전송 ──

    async def _send_command(self, agent: AgentInfo, cmd: dict) -> dict:
        """명령 WebSocket으로 전송 + 응답 직접 수신 (스트리밍과 분리)"""
        if not agent.connected or not agent.cmd_ws:
            return {"success": False, "error": "Not connected"}

        try:
            await agent.cmd_ws.send(json.dumps(cmd))
            # 명령 전용 WebSocket이라 프레임과 섞이지 않음
            response = await asyncio.wait_for(agent.cmd_ws.recv(), timeout=5)
            return json.loads(response)
        except asyncio.TimeoutError:
            return {"success": False, "error": "Timeout"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def send_command(self, name: str, action: str, params: dict = None, human_like: bool = True) -> bool:
        """명령 전송"""
        if name not in self.agents:
            print(f"[ERROR] Agent not found: {name}")
            return False

        agent = self.agents[name]
        params = params or {}
        params["human_like"] = human_like

        cmd = {
            "type": "command",
            "action": action,
            "params": params
        }

        try:
            response = self._run_async(self._send_command(agent, cmd))
            return response.get("success", False)
        except Exception as e:
            print(f"[ERROR] 명령 전송 실패: {e}")
            return False

    # ── 편의 메서드 ──

    def send_click(self, name: str, x: int, y: int, button: str = "LEFT", human_like: bool = True) -> bool:
        """마우스 클릭"""
        # 먼저 이동
        self.send_command(name, "mouse_move", {"x": x, "y": y}, human_like)
        time.sleep(0.1)
        # 클릭
        return self.send_command(name, "mouse_click", {"button": button}, human_like)

    def send_double_click(self, name: str, x: int, y: int, button: str = "LEFT", human_like: bool = True) -> bool:
        """더블 클릭"""
        self.send_command(name, "mouse_move", {"x": x, "y": y}, human_like)
        time.sleep(0.1)
        return self.send_command(name, "mouse_double_click", {"button": button}, human_like)

    def send_drag(self, name: str, from_x: int, from_y: int, to_x: int, to_y: int, human_like: bool = True) -> bool:
        """드래그"""
        return self.send_command(name, "mouse_drag", {
            "from_x": from_x, "from_y": from_y,
            "to_x": to_x, "to_y": to_y
        }, human_like)

    def send_key(self, name: str, key: str, human_like: bool = True) -> bool:
        """키 입력"""
        return self.send_command(name, "key", {"key": key}, human_like)

    def send_combo(self, name: str, keys: List[str], human_like: bool = True) -> bool:
        """조합키"""
        return self.send_command(name, "combo", {"keys": keys}, human_like)

    def send_type(self, name: str, text: str, human_like: bool = True) -> bool:
        """텍스트 입력"""
        return self.send_command(name, "type_text", {"text": text}, human_like)

    def send_wait(self, name: str, seconds: float) -> bool:
        """대기"""
        return self.send_command(name, "wait", {"seconds": seconds}, False)

    def set_active_window(self, name: str, window_id: str) -> bool:
        """활성 창 변경 (HID 명령 대상 + 창을 앞으로)"""
        if name not in self.agents:
            return False

        cmd = {"type": "set_active_window", "params": {"window_id": window_id}}
        try:
            response = self._run_async(self._send_command(self.agents[name], cmd))
            if response.get("success"):
                self.agents[name].active_window = window_id
            return response.get("success", False)
        except:
            return False

    def send_click_to_window(self, name: str, window_id: str, x: int, y: int,
                              button: str = "LEFT", human_like: bool = True) -> bool:
        """특정 창에 클릭 (활성 창 변경 시에만 활성화)"""
        if name not in self.agents:
            return False

        # 활성 창이 다를 때만 전환
        agent = self.agents[name]
        if agent.active_window != window_id:
            self.set_active_window(name, window_id)
            import time
            time.sleep(0.1)

        return self.send_click(name, x, y, button, human_like)

    def find_next_gersang(self, name: str) -> dict:
        """Agent에 새 Gersang 창 검색 요청"""
        if name not in self.agents:
            return {"success": False}
        cmd = {"type": "find_next_gersang", "params": {}}
        try:
            response = self._run_async(self._send_command(self.agents[name], cmd))
            return response
        except:
            return {"success": False}

    # ── 실시간 마우스 (fire-and-forget) ──

    async def _send_fire_and_forget(self, agent: 'AgentInfo', cmd: dict):
        """명령 전송 (응답 안 기다림)"""
        if agent.connected and agent.cmd_ws:
            try:
                await agent.cmd_ws.send(json.dumps(cmd))
            except Exception:
                pass

    def send_realtime_mouse_pos(self, name: str, x: int, y: int):
        """실시간 마우스 절대 위치 (fire-and-forget, SetCursorPos용)"""
        if name not in self.agents or not self._loop:
            return
        agent = self.agents[name]
        cmd = {"type": "realtime_mouse_pos", "params": {"x": x, "y": y}}
        asyncio.run_coroutine_threadsafe(self._send_fire_and_forget(agent, cmd), self._loop)

    # ── 화면 수신 ──

    def get_frame(self, name: str, window_id: str = None) -> Optional[np.ndarray]:
        """
        최신 프레임 가져오기
        Args:
            name: Agent 이름
            window_id: 창 ID (None이면 첫 번째 창)
        """
        if name not in self.agents:
            return None

        agent = self.agents[name]
        if not agent.windows:
            return None

        if window_id is None:
            # 첫 번째 창 반환
            window_id = next(iter(agent.windows.keys()))

        if window_id in agent.windows:
            return agent.windows[window_id].frame
        return None

    def get_all_frames(self, name: str) -> Dict[str, np.ndarray]:
        """모든 창의 프레임 가져오기"""
        if name not in self.agents:
            return {}

        result = {}
        for win_id, wf in self.agents[name].windows.items():
            if wf.frame is not None:
                result[win_id] = wf.frame
        return result

    def get_windows(self, name: str) -> Dict[str, WindowFrame]:
        """Agent의 모든 창 정보 가져오기"""
        if name not in self.agents:
            return {}
        return self.agents[name].windows

    def get_frame_time(self, name: str, window_id: str = None) -> float:
        """마지막 프레임 시간"""
        if name not in self.agents:
            return 0

        agent = self.agents[name]
        if window_id and window_id in agent.windows:
            return agent.windows[window_id].timestamp
        elif agent.windows:
            return max(wf.timestamp for wf in agent.windows.values())
        return 0

    def on_frame(self, name: str, callback: Callable):
        """프레임 수신 콜백 등록 (callback(agent_name, window_id, frame))"""
        if name in self.agents:
            self.agents[name].frame_callbacks.append(callback)

    def remove_frame_callback(self, name: str, callback: Callable):
        """프레임 콜백 제거"""
        if name in self.agents:
            try:
                self.agents[name].frame_callbacks.remove(callback)
            except ValueError:
                pass

    # ── 설정 ──

    def set_window(self, name: str, title: str) -> bool:
        """Agent 캡처 창 변경"""
        if name not in self.agents:
            return False

        agent = self.agents[name]
        cmd = {"type": "set_window", "params": {"title": title}}

        try:
            response = self._run_async(self._send_command(agent, cmd))
            if response.get("success"):
                agent.window = title
            return response.get("success", False)
        except:
            return False

    def set_fps(self, name: str, fps: int) -> bool:
        """스트리밍 FPS 변경"""
        if name not in self.agents:
            return False

        agent = self.agents[name]
        cmd = {"type": "set_fps", "params": {"fps": fps}}

        try:
            response = self._run_async(self._send_command(agent, cmd))
            if response.get("success"):
                agent.fps = fps
            return response.get("success", False)
        except:
            return False

    def set_quality(self, name: str, quality: int) -> bool:
        """스트리밍 품질 변경"""
        if name not in self.agents:
            return False

        cmd = {"type": "set_quality", "params": {"quality": quality}}
        try:
            response = self._run_async(self._send_command(self.agents[name], cmd))
            return response.get("success", False)
        except:
            return False

    def list_windows(self, name: str) -> List[str]:
        """Agent의 열린 창 목록"""
        if name not in self.agents:
            return []

        cmd = {"type": "list_windows", "params": {}}
        try:
            response = self._run_async(self._send_command(self.agents[name], cmd))
            return response.get("windows", [])
        except:
            return []

    def ping(self, name: str) -> float:
        """Agent ping (왕복 시간 반환, ms)"""
        if name not in self.agents:
            return -1

        start = time.time()
        cmd = {"type": "ping", "params": {}}
        try:
            response = self._run_async(self._send_command(self.agents[name], cmd))
            if response.get("success"):
                return (time.time() - start) * 1000
        except:
            pass
        return -1

    def __del__(self):
        """정리"""
        self.disconnect_all()
        if self._loop:
            self._loop.call_soon_threadsafe(self._loop.stop)


# ── 사용 예시 ──

if __name__ == "__main__":
    import cv2

    ctrl = RemoteController()

    # Agent 연결
    print("Agent 연결 중...")
    if ctrl.connect("127.0.0.1", 8765, "PC1"):
        print(f"연결됨! Agents: {ctrl.list_agents()}")

        # 화면 표시
        print("화면 스트리밍 시작 (ESC로 종료)...")
        while True:
            frame = ctrl.get_frame("PC1")
            if frame is not None:
                # BGR로 변환 (OpenCV용)
                frame_bgr = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
                cv2.imshow("PC1 Screen", frame_bgr)

            key = cv2.waitKey(30)
            if key == 27:  # ESC
                break

        cv2.destroyAllWindows()
        ctrl.disconnect_all()
    else:
        print("연결 실패!")
