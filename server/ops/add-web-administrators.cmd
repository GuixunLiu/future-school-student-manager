@echo off
setlocal
chcp 65001 >nul
set "WX_ACCOUNT_NODE=%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe"
if not exist "%WX_ACCOUNT_NODE%" set "WX_ACCOUNT_NODE=node.exe"
"%WX_ACCOUNT_NODE%" "%~dp0add-web-administrators.cjs" %*
set "WX_ACCOUNT_EXIT=%errorlevel%"
if /I "%~1"=="--self-check" exit /b %WX_ACCOUNT_EXIT%
echo.
pause
exit /b %WX_ACCOUNT_EXIT%
