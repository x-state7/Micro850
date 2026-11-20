# Run in elevated Windows PowerShell. Does not delete data or log files.
$ErrorActionPreference = 'Stop'
foreach ($name in @('Micro850-Collector','Micro850-Synchronizer')) {
    $task = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
    if ($task) {
        Stop-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
        Unregister-ScheduledTask -TaskName $name -Confirm:$false
        Write-Host "Removed task $name (SQLite data preserved)."
    }
}
