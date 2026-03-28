/*
  Leonardo HID Macro Controller
  - PC에서 시리얼(9600bps)로 명령을 보내면 실제 키보드/마우스 입력으로 변환
  - PC는 이 장치를 일반 USB 키보드 + 마우스로 인식
  
  명령어 프로토콜 (줄바꿈 종료):
    KEY:a                    → 'a' 키 입력
    KEY:A                    → Shift+a (대문자)
    KEYDOWN:a                → 'a' 키 누르기 (유지)
    KEYUP:a                  → 'a' 키 떼기
    SPECIAL:KEY_RETURN       → Enter, Tab 등 특수키
    COMBO:KEY_LEFT_CTRL+c    → Ctrl+C 등 조합키
    TYPE:hello world         → 문자열 타이핑
    MOUSE_MOVE:dx,dy         → 마우스 상대 이동
    MOUSE_ABS:x,y,sw,sh      → 마우스 절대 이동 (화면크기 기준 분할이동)
    MOUSE_CLICK:LEFT         → 좌클릭
    MOUSE_CLICK:RIGHT        → 우클릭
    MOUSE_CLICK:MIDDLE       → 중클릭
    MOUSE_DOWN:LEFT          → 마우스 버튼 누르기 (드래그용)
    MOUSE_UP:LEFT            → 마우스 버튼 떼기
    MOUSE_SCROLL:amount      → 스크롤 (양수=위, 음수=아래)
    DELAY:ms                 → 밀리초 대기
    RELEASE_ALL              → 모든 키/마우스 해제
    PING                     → 연결 확인 (PONG 응답)
*/
// Microsoft 기본 키보드로 위장
#define USB_VID 0x045E           // Microsoft
#define USB_PID 0x0750           // Wired Keyboard 400
#define USB_MANUFACTURER "Microsoft"
#define USB_PRODUCT "USB Keyboard"
#include <Keyboard.h>
#include <Mouse.h>

String inputBuffer = "";

// 특수키 이름 → 키코드 매핑
uint8_t getSpecialKey(String name) {
  if (name == "KEY_RETURN" || name == "KEY_ENTER") return KEY_RETURN;
  if (name == "KEY_ESC") return KEY_ESC;
  if (name == "KEY_BACKSPACE") return KEY_BACKSPACE;
  if (name == "KEY_TAB") return KEY_TAB;
  if (name == "KEY_SPACE") return ' ';
  if (name == "KEY_DELETE") return KEY_DELETE;
  if (name == "KEY_INSERT") return KEY_INSERT;
  if (name == "KEY_HOME") return KEY_HOME;
  if (name == "KEY_END") return KEY_END;
  if (name == "KEY_PAGE_UP") return KEY_PAGE_UP;
  if (name == "KEY_PAGE_DOWN") return KEY_PAGE_DOWN;
  
  // 방향키
  if (name == "KEY_UP") return KEY_UP_ARROW;
  if (name == "KEY_DOWN") return KEY_DOWN_ARROW;
  if (name == "KEY_LEFT") return KEY_LEFT_ARROW;
  if (name == "KEY_RIGHT") return KEY_RIGHT_ARROW;
  
  // 수정자 키
  if (name == "KEY_LEFT_CTRL") return KEY_LEFT_CTRL;
  if (name == "KEY_LEFT_SHIFT") return KEY_LEFT_SHIFT;
  if (name == "KEY_LEFT_ALT") return KEY_LEFT_ALT;
  if (name == "KEY_LEFT_GUI") return KEY_LEFT_GUI;  // Windows 키
  if (name == "KEY_RIGHT_CTRL") return KEY_RIGHT_CTRL;
  if (name == "KEY_RIGHT_SHIFT") return KEY_RIGHT_SHIFT;
  if (name == "KEY_RIGHT_ALT") return KEY_RIGHT_ALT;
  
  // F키
  if (name == "KEY_F1") return KEY_F1;
  if (name == "KEY_F2") return KEY_F2;
  if (name == "KEY_F3") return KEY_F3;
  if (name == "KEY_F4") return KEY_F4;
  if (name == "KEY_F5") return KEY_F5;
  if (name == "KEY_F6") return KEY_F6;
  if (name == "KEY_F7") return KEY_F7;
  if (name == "KEY_F8") return KEY_F8;
  if (name == "KEY_F9") return KEY_F9;
  if (name == "KEY_F10") return KEY_F10;
  if (name == "KEY_F11") return KEY_F11;
  if (name == "KEY_F12") return KEY_F12;
  
  // CAPS LOCK, Print Screen 등
  if (name == "KEY_CAPS_LOCK") return KEY_CAPS_LOCK;
  if (name == "KEY_PRINT_SCREEN") return 0xCE;
  
  return 0;
}

// 키 문자열 해석: 특수키 이름이면 코드 반환, 한 글자면 그 문자 반환
uint8_t resolveKey(String token) {
  token.trim();
  if (token.startsWith("KEY_")) {
    return getSpecialKey(token);
  }
  if (token.length() == 1) {
    return (uint8_t)token.charAt(0);
  }
  return 0;
}

void processCommand(String cmd) {
  cmd.trim();
  if (cmd.length() == 0) return;
  
  // --- PING ---
  if (cmd == "PING") {
    Serial.println("PONG");
    return;
  }
  
  // --- RELEASE_ALL ---
  if (cmd == "RELEASE_ALL") {
    Keyboard.releaseAll();
    Mouse.release(MOUSE_LEFT);
    Mouse.release(MOUSE_RIGHT);
    Mouse.release(MOUSE_MIDDLE);
    Serial.println("OK");
    return;
  }
  
  // 명령:파라미터 분리
  int colonIdx = cmd.indexOf(':');
  String action, param;
  if (colonIdx >= 0) {
    action = cmd.substring(0, colonIdx);
    param = cmd.substring(colonIdx + 1);
  } else {
    action = cmd;
    param = "";
  }
  
  // --- KEY (단일 키 press+release) ---
  if (action == "KEY") {
    uint8_t k = resolveKey(param);
    if (k) {
      Keyboard.press(k);
      delay(30);
      Keyboard.release(k);
    }
    Serial.println("OK");
    return;
  }
  
  // --- KEYDOWN / KEYUP ---
  if (action == "KEYDOWN") {
    uint8_t k = resolveKey(param);
    if (k) Keyboard.press(k);
    Serial.println("OK");
    return;
  }
  if (action == "KEYUP") {
    uint8_t k = resolveKey(param);
    if (k) Keyboard.release(k);
    Serial.println("OK");
    return;
  }
  
  // --- SPECIAL (특수키 단독) ---
  if (action == "SPECIAL") {
    uint8_t k = getSpecialKey(param);
    if (k) {
      Keyboard.press(k);
      delay(30);
      Keyboard.release(k);
    }
    Serial.println("OK");
    return;
  }
  
  // --- COMBO (조합키: KEY_LEFT_CTRL+c, KEY_LEFT_ALT+KEY_F4 등) ---
  if (action == "COMBO") {
    // '+' 로 분리
    const int MAX_KEYS = 6;
    uint8_t keys[MAX_KEYS];
    int keyCount = 0;
    
    String remaining = param;
    while (remaining.length() > 0 && keyCount < MAX_KEYS) {
      int plusIdx = remaining.indexOf('+');
      String token;
      if (plusIdx >= 0) {
        token = remaining.substring(0, plusIdx);
        remaining = remaining.substring(plusIdx + 1);
      } else {
        token = remaining;
        remaining = "";
      }
      uint8_t k = resolveKey(token);
      if (k) {
        keys[keyCount++] = k;
      }
    }
    
    // 순서대로 누르기
    for (int i = 0; i < keyCount; i++) {
      Keyboard.press(keys[i]);
      delay(20);
    }
    delay(30);
    Keyboard.releaseAll();
    Serial.println("OK");
    return;
  }
  
  // --- TYPE (문자열 타이핑) ---
  if (action == "TYPE") {
    Keyboard.print(param);
    Serial.println("OK");
    return;
  }
  
  // --- MOUSE_MOVE (상대 이동) ---
  if (action == "MOUSE_MOVE") {
    int commaIdx = param.indexOf(',');
    if (commaIdx >= 0) {
      int dx = param.substring(0, commaIdx).toInt();
      int dy = param.substring(commaIdx + 1).toInt();
      // Mouse.move는 -128~127 범위, 큰 값은 분할 이동
      while (dx != 0 || dy != 0) {
        int mx = constrain(dx, -127, 127);
        int my = constrain(dy, -127, 127);
        Mouse.move(mx, my, 0);
        dx -= mx;
        dy -= my;
        delay(5);
      }
    }
    Serial.println("OK");
    return;
  }
  
  // --- MOUSE_ABS (절대 이동 근사 - 화면 크기 기준 분할 이동) ---
  // 형식: MOUSE_ABS:targetX,targetY,screenWidth,screenHeight
  // 먼저 (-10000,-10000)으로 이동해 원점으로 간 뒤 목표 좌표로 이동
  if (action == "MOUSE_ABS") {
    int c1 = param.indexOf(',');
    int c2 = param.indexOf(',', c1 + 1);
    int c3 = param.indexOf(',', c2 + 1);
    if (c1 >= 0 && c2 >= 0 && c3 >= 0) {
      int tx = param.substring(0, c1).toInt();
      int ty = param.substring(c1 + 1, c2).toInt();
      // 원점으로 리셋 (큰 음수값)
      int resetX = -10000;
      int resetY = -10000;
      while (resetX != 0 || resetY != 0) {
        int mx = constrain(resetX, -127, 127);
        int my = constrain(resetY, -127, 127);
        Mouse.move(mx, my, 0);
        resetX -= mx;
        resetY -= my;
        delay(1);
      }
      delay(10);
      // 목표 좌표로 이동
      while (tx != 0 || ty != 0) {
        int mx = constrain(tx, -127, 127);
        int my = constrain(ty, -127, 127);
        Mouse.move(mx, my, 0);
        tx -= mx;
        ty -= my;
        delay(1);
      }
    }
    Serial.println("OK");
    return;
  }
  
  // --- MOUSE_CLICK ---
  if (action == "MOUSE_CLICK") {
    uint8_t btn = MOUSE_LEFT;
    if (param == "RIGHT") btn = MOUSE_RIGHT;
    else if (param == "MIDDLE") btn = MOUSE_MIDDLE;
    Mouse.click(btn);
    Serial.println("OK");
    return;
  }
  
  // --- MOUSE_DOWN / MOUSE_UP (드래그용) ---
  if (action == "MOUSE_DOWN") {
    uint8_t btn = MOUSE_LEFT;
    if (param == "RIGHT") btn = MOUSE_RIGHT;
    else if (param == "MIDDLE") btn = MOUSE_MIDDLE;
    Mouse.press(btn);
    Serial.println("OK");
    return;
  }
  if (action == "MOUSE_UP") {
    uint8_t btn = MOUSE_LEFT;
    if (param == "RIGHT") btn = MOUSE_RIGHT;
    else if (param == "MIDDLE") btn = MOUSE_MIDDLE;
    Mouse.release(btn);
    Serial.println("OK");
    return;
  }
  
  // --- MOUSE_SCROLL ---
  if (action == "MOUSE_SCROLL") {
    int amount = param.toInt();
    Mouse.move(0, 0, amount);
    Serial.println("OK");
    return;
  }
  
  // --- DELAY ---
  if (action == "DELAY") {
    delay(param.toInt());
    Serial.println("OK");
    return;
  }
  
  Serial.println("ERR:UNKNOWN_CMD");
}

void setup() {
  Keyboard.begin();
  Mouse.begin();
  Serial.begin(9600);
  while (!Serial) { ; }
  Serial.println("READY");
}

void loop() {
  while (Serial.available()) {
    char c = Serial.read();
    if (c == '\n') {
      processCommand(inputBuffer);
      inputBuffer = "";
    } else if (c != '\r') {
      inputBuffer += c;
    }
  }
}
