# Leonardo HID Macro Controller

Arduino Leonardo를 실제 USB 키보드/마우스로 사용하는 매크로 자동화 시스템

## 구성 파일

| 파일 | 설명 |
|------|------|
| `leonardo_hid_macro.ino` | Arduino 펌웨어 (Leonardo에 업로드) |
| `leonardo_controller.py` | Python 제어 모듈 (PC에서 실행) |

## 셋업 순서

### 1단계: Arduino IDE에서 펌웨어 업로드

1. Arduino IDE 실행
2. **도구 → 보드 → Arduino Leonardo** 선택
3. **도구 → 포트** 에서 Leonardo 포트 선택 (예: COM3)
4. `leonardo_hid_macro.ino` 열기
5. **업로드** 버튼 클릭
6. 시리얼 모니터에서 `READY` 메시지 확인

> ⚠️ Leonardo 업로드 중 포트가 바뀔 수 있음 → 업로드 실패 시 리셋 버튼 2번 빠르게 눌러 부트로더 진입 후 재시도

### 2단계: Python 환경 설정

```bash
pip install pyserial
```

### 3단계: 포트 확인

```python
import serial.tools.list_ports
for p in serial.tools.list_ports.comports():
    print(f"{p.device} - {p.description}")
```

### 4단계: 사용

```python
from leonardo_controller import LeonardoHID

with LeonardoHID("COM3") as hid:
    hid.type_text("Hello")
    hid.enter()
    hid.combo(["KEY_LEFT_CTRL", "s"])
```

## 명령어 레퍼런스

### 키보드

```python
hid.key("a")                              # 단일 키
hid.key("KEY_RETURN")                     # Enter
hid.combo(["KEY_LEFT_CTRL", "c"])         # Ctrl+C
hid.combo(["KEY_LEFT_CTRL", "KEY_LEFT_SHIFT", "KEY_ESC"])  # Ctrl+Shift+Esc
hid.type_text("hello world")             # 문자열 타이핑
hid.key_down("KEY_LEFT_SHIFT")           # Shift 누르고 유지
hid.key("a")                              # → 'A' 입력됨
hid.key_up("KEY_LEFT_SHIFT")             # Shift 떼기
```

### 마우스

```python
hid.mouse_move(100, -50)                  # 상대 이동 (오른쪽100, 위50)
hid.mouse_move_to(960, 540)              # 절대 이동 (FHD 중앙)
hid.mouse_click()                         # 좌클릭
hid.mouse_click("RIGHT")                 # 우클릭
hid.mouse_double_click()                 # 더블클릭
hid.mouse_scroll(-3)                     # 아래로 스크롤
hid.mouse_drag(100, 100, 500, 300)       # 드래그
```

### 특수키 목록

| 이름 | 설명 |
|------|------|
| `KEY_RETURN` / `KEY_ENTER` | Enter |
| `KEY_ESC` | Escape |
| `KEY_BACKSPACE` | Backspace |
| `KEY_TAB` | Tab |
| `KEY_SPACE` | Space |
| `KEY_DELETE` | Delete |
| `KEY_INSERT` | Insert |
| `KEY_HOME` / `KEY_END` | Home / End |
| `KEY_PAGE_UP` / `KEY_PAGE_DOWN` | Page Up / Down |
| `KEY_UP` / `KEY_DOWN` / `KEY_LEFT` / `KEY_RIGHT` | 방향키 |
| `KEY_LEFT_CTRL` / `KEY_LEFT_SHIFT` / `KEY_LEFT_ALT` | 수정자 키 |
| `KEY_LEFT_GUI` | Windows 키 |
| `KEY_F1` ~ `KEY_F12` | 펑션 키 |
| `KEY_CAPS_LOCK` | Caps Lock |

## Robot Framework 연동 예시

```python
# leonardo_rf_library.py
from leonardo_controller import LeonardoHID

class LeonardoKeywords:
    def __init__(self, port="COM3"):
        self.hid = LeonardoHID(port)
    
    def press_key(self, key):
        self.hid.key(key)
    
    def press_combo(self, *keys):
        self.hid.combo(list(keys))
    
    def type_string(self, text):
        self.hid.type_text(text)
    
    def click_at(self, x, y):
        self.hid.mouse_move_to(int(x), int(y))
        self.hid.wait(0.1)
        self.hid.mouse_click()
    
    def close_connection(self):
        self.hid.close()
```

```robot
*** Settings ***
Library    leonardo_rf_library.py    COM3

*** Test Cases ***
Open Notepad And Type
    Press Combo    KEY_LEFT_GUI    r
    Sleep    0.5s
    Type String    notepad
    Press Key    KEY_RETURN
    Sleep    1s
    Type String    Test input from Leonardo
    Press Combo    KEY_LEFT_CTRL    s
```

## 장치 관리자에서 확인

Leonardo를 연결하면 다음과 같이 인식됩니다:
- **키보드** → HID 키보드 장치
- **마우스** → HID 규격 마우스
- **포트(COM & LPT)** → Arduino Leonardo (COMx)

일반 USB 키보드/마우스와 동일한 드라이버를 사용하므로 소프트웨어 수준에서 구분 불가

## 주의사항

- **한글 입력 불가**: USB HID 키보드는 키코드 기반이라 한글 직접 입력이 안 됨. 한글이 필요하면 클립보드(Ctrl+V) 활용
- **마우스 절대 좌표**: 상대 이동 기반 근사치이므로 오차 가능. 정밀한 절대 좌표가 필요하면 별도 보정 필요
- **업로드 후 키보드 난입 주의**: 펌웨어에 자동 타이핑 코드가 있으면 업로드 직후 입력이 발생할 수 있음. setup()에는 자동 입력 넣지 말 것
