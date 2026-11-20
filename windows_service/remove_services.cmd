@echo off
setlocal EnableExtensions
for %%I in ("%~dp0..") do set "ROOT=%%~fI"
set "NSSM=%ROOT%\tools\nssm.exe"
fltmc >nul 2>&1
if errorlevel 1 (
  echo ERROR: Run Command Prompt as Administrator.
  exit /b 1
)
if not exist "%NSSM%" (
  echo ERROR: NSSM not found at "%NSSM%"
  exit /b 1
)
sc.exe stop Micro850Collector >nul 2>&1
sc.exe stop Micro850Sync >nul 2>&1
"%NSSM%" remove Micro850Collector confirm
"%NSSM%" remove Micro850Sync confirm
echo Service registration removed. SQLite and .env were NOT deleted.
