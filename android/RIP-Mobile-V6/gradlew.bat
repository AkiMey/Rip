@echo off
setlocal EnableExtensions
set "ROOT=%~dp0"
set "GRADLE_VERSION=8.9"
set "CACHE=%USERPROFILE%\.gradle\rip-mobile-bootstrap\gradle-%GRADLE_VERSION%"
set "GRADLE=%CACHE%\bin\gradle.bat"
if exist "%GRADLE%" goto run

echo [RIP Mobile] Gradle %GRADLE_VERSION% ilk kez indiriliyor...
set "ZIP=%TEMP%\rip-gradle-%GRADLE_VERSION%.zip"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; Invoke-WebRequest -UseBasicParsing 'https://services.gradle.org/distributions/gradle-%GRADLE_VERSION%-bin.zip' -OutFile '%ZIP%'; New-Item -ItemType Directory -Force -Path '%USERPROFILE%\.gradle\rip-mobile-bootstrap' | Out-Null; Expand-Archive -LiteralPath '%ZIP%' -DestinationPath '%USERPROFILE%\.gradle\rip-mobile-bootstrap' -Force"
if errorlevel 1 exit /b 1
:run
call "%GRADLE%" -p "%ROOT%" %*
exit /b %ERRORLEVEL%
