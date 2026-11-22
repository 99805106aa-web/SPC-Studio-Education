@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ============================================
title SPC Studio
echo   SPC Studio · 계량형 / 계수형 시작
echo ============================================
echo.

REM 가상환경이 있으면 활성화
if exist "venv\Scripts\activate.bat" (
    call venv\Scripts\activate.bat
    echo [INFO] 가상환경 활성화됨
) else if exist ".venv\Scripts\activate.bat" (
    call .venv\Scripts\activate.bat
    echo [INFO] 가상환경 활성화됨
)

echo [INFO] Streamlit 앱을 시작합니다...
echo [INFO] 브라우저에서 http://localhost:8502 로 접속하세요.
echo.
python -m streamlit run app.py --server.address 127.0.0.1 --server.port 8502
pause
