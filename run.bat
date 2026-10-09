@echo off
chcp 65001 >nul
title Meinya Video
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" goto tao
rem .venv co roi chua chac da cai du: lan truoc co the bi ngat giua chung (mat mang, dong cua so)
".venv\Scripts\python.exe" -c "import webview, yt_dlp" >nul 2>&1
if not errorlevel 1 goto chay
echo [*] Thu vien chua cai du (lan truoc bi ngat giua chung?): cai tiep...
goto cai

:tao
echo [*] Lan dau chay: tao moi truong .venv va cai thu vien loi da ghim...
py -3 -m venv .venv
if errorlevel 1 (
    echo [!] Khong tao duoc .venv. Can Python 3.10 tro len, tai tu python.org, nho tick Add python.exe to PATH.
    pause
    exit /b 1
)

:cai
echo [*] Dang tai thu vien, co the mat vai phut. Dung dong cua so nay.
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
