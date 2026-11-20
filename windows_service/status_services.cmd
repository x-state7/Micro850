@echo off
setlocal
for %%I in ("%~dp0..") do set "ROOT=%%~fI"
echo [Collector service]
sc.exe query Micro850Collector
echo.
echo [Sync service]
sc.exe query Micro850Sync
echo.
echo [SQLite queue]
"%ROOT%\.venv\Scripts\python.exe" -m plc_sync status
echo.
echo Log files: %ROOT%\logs\collect.log and sync.log
