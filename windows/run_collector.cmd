@echo off
setlocal EnableExtensions
cd /d "%~dp0.." || exit /b 2
if not exist ".venv\Scripts\python.exe" (
  echo ERROR: Python venv missing. Run: py -3.11 -m venv .venv
  exit /b 2
)
if not exist logs mkdir logs
:RUN
".venv\Scripts\python.exe" -u -m plc_sync --debug collect
set "RC=%ERRORLEVEL%"
if "%RC%"=="42" (
  echo %DATE% %TIME% collector STOPPED: fatal data identity conflict ^(42^). >> logs\windows_collector_supervisor.log
  exit /b 42
)
echo %DATE% %TIME% collector exited code=%RC%; restarting in 10 seconds. >> logs\windows_collector_supervisor.log
timeout /t 10 /nobreak >nul
goto RUN
