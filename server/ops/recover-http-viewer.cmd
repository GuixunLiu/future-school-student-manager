@echo off
setlocal
chcp 65001 >nul
if /I "%~1"=="--self-check" (
  "C:\Users\liugu\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe" "%~dp0patch-http-viewer.cjs" --self-check
  exit /b
)
"C:\Users\liugu\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe" "%~dp0patch-http-viewer.cjs" --recover-original
set "WXREVIEW_EXIT=%errorlevel%"
echo.
pause
exit /b %WXREVIEW_EXIT%
