@echo off
setlocal EnableExtensions
chcp 65001 >nul 2>&1
cd /d "%~dp0\.."
set "PY=%CD%\sistem\.venv\Scripts\python.exe"
if not exist "%PY%" (
  echo [HATA] Venv yok. Once BASLAT.bat calistir.
  pause
  exit /b 1
)
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8:replace"
pushd "%CD%\sistem"
"%PY%" main.py --selftest
set "RC=%ERRORLEVEL%"
popd
echo.
echo Rapor: %%LOCALAPPDATA%%\RIPPERINIO\ripperinio_tani.log
pause
exit /b %RC%
