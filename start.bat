@echo off
setlocal
cd /d "%~dp0"
title SPC Studio

set "PYTHON_EXE="
if exist "venv\Scripts\python.exe" set "PYTHON_EXE=venv\Scripts\python.exe"
if not defined PYTHON_EXE if exist ".venv\Scripts\python.exe" set "PYTHON_EXE=.venv\Scripts\python.exe"

if not defined PYTHON_EXE (
    where py >nul 2>&1
    if not errorlevel 1 set "PYTHON_EXE=py -3"
)

if not defined PYTHON_EXE (
    where python >nul 2>&1
    if not errorlevel 1 set "PYTHON_EXE=python"
)

if not defined PYTHON_EXE (
    echo Python was not found on this PC.
    echo Install Python from python.org and turn on Add python.exe to PATH.
    echo Or, in this folder, run:
    echo   py -3 -m venv venv
    echo   venv\Scripts\python.exe -m pip install -r requirements.txt
    pause
    exit /b 1
)

echo Starting SPC Studio at http://127.0.0.1:8502
%PYTHON_EXE% -m streamlit run app.py --server.address 127.0.0.1 --server.port 8502
pause
exit /b %errorlevel%
