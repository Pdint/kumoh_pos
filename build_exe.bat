@echo off
chcp 65001 > nul
REM ===== 금오분식 POS - Windows 실행 파일(exe) 빌드 =====
REM 필요: Python 3.9 이상 (python.org 설치 시 tcl/tk 포함 옵션 체크)
cd /d "%~dp0"

echo [1/3] PyInstaller 설치/업데이트...
python -m pip install --upgrade pyinstaller || goto :error

echo [2/3] 테스트 실행...
python -m unittest discover -s tests || goto :error

echo [3/3] exe 빌드...
python -m PyInstaller --noconfirm --clean kumoh_pos.spec || goto :error

echo.
echo 완료: dist\KumohPOS.exe
pause
exit /b 0

:error
echo.
echo 빌드 실패. 위 오류 메시지를 확인하세요.
pause
exit /b 1
