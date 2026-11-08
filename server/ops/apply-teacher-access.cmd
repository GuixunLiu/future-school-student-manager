@echo off
setlocal
chcp 65001 >nul
"C:\Users\liugu\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe" "%~dp0apply-teacher-access.cjs" %*
set "WXREVIEW_EXIT=%errorlevel%"
if /I "%~1"=="--self-check" exit /b %WXREVIEW_EXIT%
echo.
pause
exit /b %WXREVIEW_EXIT%
