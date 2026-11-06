@echo off
setlocal
chcp 65001 >nul
set "WX_WEB_NODE=%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe"
if not exist "%WX_WEB_NODE%" set "WX_WEB_NODE=node.exe"
"%WX_WEB_NODE%" "%~dp0deploy-teacher-web.cjs" %*
set "WX_WEB_EXIT=%errorlevel%"
if /I "%~1"=="--self-check" exit /b %WX_WEB_EXIT%
echo.
pause
exit /b %WX_WEB_EXIT%
