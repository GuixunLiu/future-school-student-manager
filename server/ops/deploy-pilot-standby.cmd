@echo off
setlocal
chcp 65001 >nul
set "WX_PILOT_NODE=%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe"
if not exist "%WX_PILOT_NODE%" set "WX_PILOT_NODE=node.exe"
"%WX_PILOT_NODE%" "%~dp0deploy-pilot-standby.cjs" %*
set "WX_PILOT_EXIT=%errorlevel%"
if /I "%~1"=="--self-check" exit /b %WX_PILOT_EXIT%
echo.
pause
exit /b %WX_PILOT_EXIT%
