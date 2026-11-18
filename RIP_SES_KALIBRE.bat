@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title RIP V6 - HEY SES KALIBRASYONU
set "PY=%CD%\sistem\.venv\Scripts\python.exe"
if not exist "%PY%" (
  echo [HATA] sistem\.venv\Scripts\python.exe bulunamadi.
  pause
  exit /b 1
)
"%PY%" "%CD%\RIP_SES_KALIBRE.py"
echo.
pause
