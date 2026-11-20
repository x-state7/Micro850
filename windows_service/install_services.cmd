@echo off
setlocal EnableExtensions
for %%I in ("%~dp0..") do set "ROOT=%%~fI"
set "NSSM=%ROOT%\tools\nssm.exe"
set "PY=%ROOT%\.venv\Scripts\python.exe"
set "COL=Micro850Collector"
set "SYN=Micro850Sync"

echo ============================================
echo Micro850 - Windows Services installer
echo Project: %ROOT%
echo No Python venv activation. No Task Scheduler.
echo ============================================

fltmc >nul 2>&1
if errorlevel 1 (
 echo ERROR: Run Command Prompt as Administrator.
  exit /b 1
)
if not exist "%NSSM%" (
 echo ERROR: NSSM not found at "%NSSM%"
 echo Get the 64-bit nssm.exe from https://nssm.cc/download
 echo and save it to %ROOT%\tools\nssm.exe
  exit /b 1
)
if not exist "%PY%" (
 echo ERROR: Python not found at "%PY%"
 echo Run from %ROOT%: py -m venv .venv
 echo Then: .\.venv\Scripts\python.exe -m pip install -r requirements.txt
  exit /b 1
)
if not exist "%ROOT%\.env" (
 echo ERROR: %ROOT%\.env is missing. Keep your current working .env.
  exit /b 1
)
if not exist "%ROOT%\config\settings.json" (
 echo ERROR: config\settings.json missing.
  exit /b 1
)
if not exist "%ROOT%\logs" mkdir "%ROOT%\logs"

sc.exe query "%COL%" >nul 2>&1
if not errorlevel 1 (
 echo ERROR: Service %COL% already exists; nothing changed.
 echo Check status with: sc.exe query %COL%
  exit /b 1
)
sc.exe query "%SYN%" >nul 2>&1
if not errorlevel 1 (
 echo ERROR: Service %SYN% already exists; nothing changed.
 echo Check status with: sc.exe query %SYN%
  exit /b 1
)

echo Installing collector service...
"%NSSM%" install "%COL%" "%PY%" -u -m plc_sync collect
if errorlevel 1 goto FAIL
"%NSSM%" set "%COL%" AppDirectory "%ROOT%"
if errorlevel 1 goto FAIL
"%NSSM%" set "%COL%" DisplayName "Micro850 PLC Collector"
"%NSSM%" set "%COL%" Description "Read-only Micro850 collection into durable SQLite queue"
"%NSSM%" set "%COL%" Start SERVICE_DELAYED_AUTO_START
"%NSSM%" set "%COL%" AppExit Default Restart
"%NSSM%" set "%COL%" AppExit 42 Exit
"%NSSM%" set "%COL%" AppRestartDelay 10000

echo Installing MariaDB synchronizer service...
"%NSSM%" install "%SYN%" "%PY%" -u -m plc_sync sync
if errorlevel 1 goto FAIL
"%NSSM%" set "%SYN%" AppDirectory "%ROOT%"
if errorlevel 1 goto FAIL
"%NSSM%" set "%SYN%" DisplayName "Micro850 MariaDB Synchronizer"
"%NSSM%" set "%SYN%" Description "Replay unsynced SQLite samples to MariaDB in ascending order"
"%NSSM%" set "%SYN%" Start SERVICE_DELAYED_AUTO_START
"%NSSM%" set "%SYN%" AppExit Default Restart
"%NSSM%" set "%SYN%" AppExit 42 Exit
"%NSSM%" set "%SYN%" AppRestartDelay 10000

echo.
echo Installation complete. These services start after reboot.
echo To start now:
echo    sc.exe start Micro850Collector
echo    sc.exe start Micro850Sync
echo To inspect:
echo    sc.exe query Micro850Collector
echo    sc.exe query Micro850Sync
echo Logs: %ROOT%\logs\collect.log and sync.log
echo.
echo WARNING: By default NSSM services run as LocalSystem; for production
echo change Log On in services.msc to a restricted service account.
exit /b 0
:FAIL
echo ERROR: Installation failed. Some services may be partially registered.
echo Review services.msc and the Windows Event Viewer; do not delete SQLite data.
exit /b 1
