@echo off
setlocal EnableExtensions
chcp 65001 >nul 2>&1
cd /d "%~dp0"
title R.I.P V6.2.1

set "SYSTEM=%CD%\sistem"
set "PY=%SYSTEM%\.venv\Scripts\python.exe"
set "PYW=%SYSTEM%\.venv\Scripts\pythonw.exe"
set "HIDDEN_LAUNCHER=%CD%\scripts\START_RIP_HIDDEN.py"

if not exist "%SYSTEM%\main.py" (
  echo [HATA] sistem\main.py bulunamadi.
  pause
  exit /b 1
)

REM Ilk kurulum gerekiyorsa bu pencere sadece kurulum boyunca gorunur.
if not exist "%PY%" (
  echo [RIP V6] Ilk kurulum yapiliyor...
  powershell -NoProfile -ExecutionPolicy Bypass -File "%CD%\scripts\INSTALL.ps1"
  if errorlevel 1 (
    echo.
    echo [HATA] Kurulum tamamlanamadi.
    pause
    exit /b 1
  )
)

if not exist "%PYW%" (
  echo [HATA] pythonw.exe bulunamadi: %PYW%
  pause
  exit /b 1
)
if not exist "%HIDDEN_LAUNCHER%" (
  echo [HATA] Gizli baslatici bulunamadi: %HIDDEN_LAUNCHER%
  pause
  exit /b 1
)

REM V6.2.1: Her elle baslatmada otomatik baslangic kayitlarini sessizce
REM self-heal et. Boylece yeni RIP klasorune gecildiginde Task Scheduler
REM eski surum yolunu kullanmaya devam etmez.
powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%CD%\scripts\AUTOSTART.ps1" -Quiet >nul 2>&1

REM Launcher pythonw ile gorunmez acilir; asil RIP python.exe + CREATE_NO_WINDOW
REM kullanir. Boylece terminal gorunmez ama hata kaydi kaybolmaz.
start "" /b "%PYW%" "%HIDDEN_LAUNCHER%"
exit /b 0
