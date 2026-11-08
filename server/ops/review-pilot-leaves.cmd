@echo off
setlocal
chcp 65001 >nul
set "WX_LEAVE_NODE=%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe"
if not exist "%WX_LEAVE_NODE%" set "WX_LEAVE_NODE=node.exe"
"%WX_LEAVE_NODE%" "%~dp0review-pilot-leaves.cjs" %*
set "WX_LEAVE_EXIT=%errorlevel%"
if /I "%~1"=="--self-check" exit /b %WX_LEAVE_EXIT%
echo.
pause
exit /b %WX_LEAVE_EXIT%
