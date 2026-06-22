@echo off
setlocal

if "%~1"=="--help" goto help
if "%~1"=="-h" goto help
if "%~1"=="/?" goto help

set "PORT=%~1"
if "%PORT%"=="" set "PORT=8765"
set "HOST=%~2"
if "%HOST%"=="" set "HOST=127.0.0.1"

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0debug-viewer.ps1" -Port "%PORT%" -HostName "%HOST%"
exit /b %ERRORLEVEL%

:help
echo Usage: scripts\debug-viewer.cmd [port] [host]
echo Example: scripts\debug-viewer.cmd 8765
echo If the requested port is busy, the script automatically uses the next free port.
exit /b 0
