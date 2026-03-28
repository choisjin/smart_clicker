# Smart Clicker - Project Instructions

## Project Overview
Arduino Leonardo HID 기반 원격 멀티PC 제어 시스템.
화면 캡처 + 템플릿 매칭으로 UI 요소를 찾고, 실제 USB HID 입력으로 자동화.

## Architecture

```
Control PC (PyQt6 GUI + RemoteController)
    ↕ WebSocket (영상 스트리밍 + 명령)
Target PC (RemoteAgent + LeonardoHID)
    ↕ Serial 9600bps
Arduino Leonardo (USB HID Keyboard/Mouse)
```

## Key Files

| File | Role |
|------|------|
| `agent.py` | Target PC WebSocket 서버, 화면 캡처, HID 명령 실행 |
| `controller.py` | Control PC WebSocket 클라이언트, 멀티 에이전트 관리 |
| `leonardo_controller.py` | Arduino Serial 통신 래퍼, 사람 유사 입력 패턴 |
| `target_finder.py` | OpenCV 템플릿 매칭 + SmartClicker API |
| `gui/main_window.py` | PyQt6 제어 GUI |
| `leonardo_hid_macro/leonardo_hid_macro.ino` | Arduino 펌웨어 |
| `actions/macros.json` | 매크로 액션 정의 |

## Tech Stack
- **Python**: PyQt6, websockets (async), opencv-python, pyserial, mss, pywin32
- **Arduino**: Leonardo HID (Keyboard.h, Mouse.h)
- **Protocol**: WebSocket (JSON), Serial 9600bps

## Development Rules

### Code Style
- 주석/변수명: 한국어 허용
- 비동기: asyncio + 백그라운드 스레드 패턴 유지
- Serial 프로토콜: `COMMAND:PARAMS\n` → `OK\n` 형식 유지

### Architecture Constraints
- Agent(서버)와 Controller(클라이언트)의 역할 분리 유지
- LeonardoHID는 독립 모듈로 유지 (다른 프로젝트에서도 재사용 가능)
- 화면 캡처는 PrintWindow API 우선, mss 폴백
- Human-like 입력 패턴 보존 (Bezier curve, jitter, random delay)

### Testing
- Arduino 연결 없이도 테스트 가능하도록 mock 지원
- WebSocket 통신은 localhost로 테스트

### Safety
- 한글 입력은 HID 불가 → 클립보드(Ctrl+V) 활용
- Leonardo 펌웨어 setup()에 자동 입력 코드 금지
- 마우스 절대좌표는 해상도 기반 근사치 (보정 필요할 수 있음)
