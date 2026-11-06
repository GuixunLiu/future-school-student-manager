@echo off
setlocal
chcp 65001 >nul
set "WX_SCOPE_NODE=%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe"
if not exist "%WX_SCOPE_NODE%" set "WX_SCOPE_NODE=node.exe"
"%WX_SCOPE_NODE%" "%~dp0deploy-scoped-pilot.cjs" %*
set "WX_SCOPE_EXIT=%errorlevel%"
if /I "%~1"=="--self-check" exit /b %WX_SCOPE_EXIT%
echo.
pause
exit /b %WX_SCOPE_EXIT%
