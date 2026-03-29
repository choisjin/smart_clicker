@echo off
:: RDP 세션을 콘솔로 전환 (화면 잠금 없이 원격 해제)
:: 관리자 권한 필요
for /f "skip=1 tokens=3" %%s in ('query user %USERNAME%') do (
    %windir%\System32\tscon.exe %%s /dest:console
)

:: venv 활성화 + agent 실행
cd /d E:\Project\smart_clicker
call venv\Scripts\activate.bat
python agent.py
