@echo off
setlocal
chcp 65001 >nul
set "WX_REPORT_NODE=%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe"
if not exist "%WX_REPORT_NODE%" set "WX_REPORT_NODE=node.exe"
"%WX_REPORT_NODE%" "%~dp0patch-teacher-web-report.cjs" %*
set "WX_REPORT_EXIT=%errorlevel%"
if /I "%~1"=="--self-check" exit /b %WX_REPORT_EXIT%
echo.
pause
exit /b %WX_REPORT_EXIT%
