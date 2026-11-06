@echo off
setlocal
chcp 65001 >nul
set "WX_STAGE_NODE=%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe"
if not exist "%WX_STAGE_NODE%" set "WX_STAGE_NODE=node.exe"
"%WX_STAGE_NODE%" "%~dp0stage-single-class-pilot.cjs" %*
set "WX_STAGE_EXIT=%errorlevel%"
if /I "%~1"=="--self-check" exit /b %WX_STAGE_EXIT%
echo.
pause
exit /b %WX_STAGE_EXIT%
