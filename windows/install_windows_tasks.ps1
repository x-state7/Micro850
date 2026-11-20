# OPTIONAL: Elevated PowerShell. Registers both tasks at system startup.
# Runs as LocalSystem so no interactive user logon is needed. LocalSystem is
# highly privileged: prefer a dedicated least-privilege account where feasible.
# Install only after manual collect/sync tests have passed.
# Windows PowerShell 5.1+ required (ScheduledTasks module).
$ErrorActionPreference = 'Stop'
$Root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$Python = Join-Path $Root '.venv\Scripts\python.exe'
if (-not (Test-Path $Python)) { throw "Virtual environment missing at $Python" }
if (-not (Test-Path (Join-Path $Root '.env'))) { throw 'Missing .env; configure it before scheduling.' }
$who = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
if (-not $who.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Open Windows PowerShell as Administrator before running this installer.'
}
$trigger = New-ScheduledTaskTrigger -AtStartup
$settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -MultipleInstances IgnoreNew `
    -StartWhenAvailable `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -RestartCount 3
foreach ($item in @(
    @{ Name = 'Micro850-Collector'; Script = 'run_collector.cmd' },
    @{ Name = 'Micro850-Synchronizer'; Script = 'run_sync.cmd' }
)) {
    $bat = Join-Path (Join-Path $Root 'windows') $item.Script
    $arguments = '/d /c ""' + $bat + '""'
    $action = New-ScheduledTaskAction -Execute "$env:WINDIR\System32\cmd.exe" -Argument $arguments -WorkingDirectory $Root
    Register-ScheduledTask -TaskName $item.Name -Action $action -Trigger $trigger `
        -Settings $settings -User 'SYSTEM' -Force | Out-Null
    Write-Host "Registered $($item.Name) at system boot."
}
Write-Host 'Review task settings/security. After validation: Start-ScheduledTask -TaskName Micro850-Collector; Start-ScheduledTask -TaskName Micro850-Synchronizer'
