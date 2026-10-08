@echo off
chcp 65001 >nul
title Meinya Video
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" goto chay

echo [*] Lan dau chay: tao moi truong .venv va cai thu vien loi da ghim...
py -3 -m venv .venv
if errorlevel 1 (
    echo [!] Khong tao duoc .venv. Can Python 3.10 tro len, tai tu python.org, nho tick Add python.exe to PATH.
    pause
    exit /b 1
)
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
    echo [!] Cai thu vien loi. Xem thong bao phia tren, roi chay lai run.bat.
    pause
    exit /b 1
)

:chay
echo [*] Mo Meinya Video. Dong cua so nay la tat app.
".venv\Scripts\python.exe" main.py
if errorlevel 1 pause
