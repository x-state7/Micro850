# Micro850 Micro850Collector + Micro850Sync: Windows 24/7 without Task Scheduler

**Target**: Rockwell Micro850 `192.168.2.8` -> local SQLite -> `ptechview_machine` database on MariaDB `65.20.79.196`.

**Requirements**: Windows 10 or 11, working `C:\Micro850` codebase, administrator rights for installing services, Python virtual environment with dependencies, real PLC port reachable on Ethernet, power plan configured to avoid sleep. NSSM is a third-party Windows service wrapper, downloaded by you separately. No Docker, no WSL, no Task Scheduler, no `Activate.ps1` or `source` commands.

## A. Before installing (keep your real data!)

1. Never delete, overwrite, or extract a replacement `data\micro850_edge.sqlite3`, `.env`, `config\settings.json`, or `config\tags.json` from a template over your working project. Your real SQLite queue and existing `root` credentials may be there.
2. Copy the **windows_service** folder from this package into `C:\Micro850\windows_service`. Installing just these scripts is enough for the current version.
3. Stop any `run_collector.cmd`, `run_sync.cmd`, `python -m plc_sync collect`, or `python -m plc_sync sync` processes before starting services.
4. Check that Python runs **without activation** by opening PowerShell and entering:

```powershell
cd C:\Micro850
.\.venv\Scripts\python.exe --version
.\.venv\Scripts\python.exe -m plc_sync status
.\.venv\Scripts\python.exe -m plc_sync remote-check
.\.venv\Scripts\python.exe -m plc_sync --debug --trace-tags probe
```

The probe must read real PLC tags. A successful remote-check proves the database is accessible (but is not proof of new samples). For testing an actual write: `collect-once`, `sync-once`, `status`, `remote-check` as in the main README.

## B. Download NSSM (one-time)

1. Download the Windows-compatible 64-bit NSSM release from <https://nssm.cc/download>. Prefer the Windows 10+ fixed build listed by the upstream project; 2.24 final has a documented Windows 10+ startup issue.
2. Extract it. Copy **only** `win64\nssm.exe` to `C:\Micro850\tools\nssm.exe` (do not copy `nssm.exe` to system folders).
3. Keep `nssm.exe` there permanently. NSSM is the executable Windows registers for the service.

Do not run unknown binaries from arbitrary download sites. Check digital signatures/hashes where the publisher provides them.

## C. Install as auto-starting services

Open **Command Prompt (cmd.exe) as Administrator** (right-click -> Run as administrator), then:

```cmd
cd /d C:\Micro850
windows_service\install_services.cmd
```

The script creates **two distinct Windows services**:

- `Micro850Collector` runs `C:\Micro850\.venv\Scripts\python.exe -u -m plc_sync collect` in `C:\Micro850`.
- `Micro850Sync` runs `C:\Micro850\.venv\Scripts\python.exe -u -m plc_sync sync` in `C:\Micro850`.

Settings: delayed-automatic start after Windows boots; restart after unexpected exits with a 10-second minimum delay; **stop on exit code 42** to preserve data integrity if a remote payload conflict is detected. This will not install if either service name already exists. Windows service installation requires administrator rights.

Then start services immediately:

```cmd
sc.exe start Micro850Collector
sc.exe start Micro850Sync
sc.exe query Micro850Collector
sc.exe query Micro850Sync
```

A working service reports `STATE : 4 RUNNING`. During a restart delay the service may temporarily show `PAUSED`. Use Windows `services.msc` to view or manually stop/start services. **No PowerShell execution policy changes are required.**

**Security**: NSSM defaults to the Windows `LocalSystem` account, which has extensive privileges. This is easy for lab commissioning but not the preferred production identity. For production, create a restricted Windows service account and set the **Log On** identity for both services via `services.msc`. That identity must have read access to `.env`, the project/config files and execute access to `.venv`, plus read/write access to `data` and `logs`. A restricted MariaDB account with only `INSERT`/`SELECT`, verified TLS or a VPN, and appropriate host restrictions is strongly recommended; avoid `root` with TLS disabled over the public Internet.

## D. Verify collection and sync

Open PowerShell as a normal user:

```powershell
cd C:\Micro850
Get-Service Micro850Collector,Micro850Sync
Get-Content .\logs\collect.log -Tail 30 -Wait
```

In another PowerShell window:

```powershell
cd C:\Micro850
Get-Content .\logs\sync.log -Tail 30 -Wait
```

To inspect queue without activating venv:

```powershell
cd C:\Micro850
.\.venv\Scripts\python.exe -m plc_sync status
.\.venv\Scripts\python.exe -m plc_sync remote-check
```

`collect.log` should show `SQLITE_SAVED edge_id=...`, `POLL_END`, and a nonzero valid count; `sync.log` should show `MARIADB_CONNECTED`, `SYNC_BATCH_COMPLETE` and `SYNC_QUEUE_STATUS`. The logs are rotating (`5 MB`, three backups) and are written by Python regardless of whether a terminal window is visible. `--show-values` is not enabled for continuous services to avoid sensitive/process data filling logs.

## E. Make the laptop stay awake

1. Connect AC power. Set Windows **Sleep: Never** while plugged in.
2. In an elevated terminal:

```cmd
powercfg /change standby-timeout-ac 0
powercfg /change hibernate-timeout-ac 0
```

3. Review *Control Panel > Power Options > Choose what closing the lid does*, and if the lid will be closed, select *Do nothing* for plugged-in operation while maintaining adequate cooling. Disable/avoid unintended sleep. Let the display turn off normally to conserve energy.
4. Windows automatic reboots, power failure, forced shutdown, or sleep stop live PLC polling. Windows services auto-start again on the **next boot**, but they cannot reconstruct readings that were never collected. A UPS is advisable for critical monitoring.

## F. Test the two recovery scenarios

**Internet loss** (while the PLC Ethernet stays connected): check `status` before and after disabling Wi-Fi. `saved` and `queued` should increase while Internet is down. Restore Wi-Fi and watch `sync.log` for `MARIADB_RECOVERED`, then `queued` should eventually return to `0`. If MariaDB is temporarily unavailable at boot the service continues retrying; no reboot is needed when Internet returns.

**Windows restart**: with permission to reboot, restart Windows. Do *not* open a manual collector or sync terminal. Check `Get-Service Micro850Collector,Micro850Sync` and recent timestamps in `logs\collect.log` and `logs\sync.log`. Check `status` and remote-check. Both should resume from the existing SQLite IDs. The services start without interactive login.

## G. Maintenance

```cmd
sc.exe stop Micro850Collector
sc.exe stop Micro850Sync
sc.exe start Micro850Collector
sc.exe start Micro850Sync
```

To change `.env` or tag config, stop services, edit safely, then restart. To remove services **without removing stored data**, open CMD as Administrator:

```cmd
cd /d C:\Micro850
windows_service\remove_services.cmd
```

**Backup SQLite** while running (online backup, preserves WAL consistency):

```powershell
cd C:\Micro850
.\.venv\Scripts\python.exe tools\backup_sqlite.py data\micro850_edge.sqlite3 backups\micro850_edge_backup.sqlite3
```

Never delete the queue, SQLite `-wal`/`-shm` files, or change the `device_id` to conceal sync failures.

## H. No admin rights? Login-based alternative (not equivalent)

Windows Startup Folder needs no service-install privileges but runs **only after the user signs in**, not immediately at boot. Open `Win+R`, enter `shell:startup`, and put shortcuts to `C:\Micro850\windows\run_collector.cmd` and `C:\Micro850\windows\run_sync.cmd` there. These launcher scripts directly invoke `.venv\Scripts\python.exe` and restart after nonfatal exits. Do **not** run Startup Folder launchers alongside the services. A Windows login must occur after a reboot; no guaranteed headless 24/7 operation without an administrator-approved startup mechanism.

## Troubleshooting

| Issue | Explanation/action |
|---|---|
| `Activate.ps1 cannot be loaded` | Irrelevant: never activate; use `.venv\Scripts\python.exe` directly. |
| `Access is denied` installing services | Open cmd.exe **as Administrator**. Service installation needs elevated rights. |
| `nssm.exe missing` | Download official 64-bit NSSM and copy to `tools\nssm.exe`. |
| `service started then stopped` | Read `logs\collect.log`, `logs\sync.log`, and Windows Event Viewer > Windows Logs > Application (NSSM messages). Check root path and service Log On identity. |
| Exit code `42` from synchronizer | Manual investigation required: duplicate `(device_id,edge_id)` with *different* payload. NSSM intentionally stops this service. |
| Restarting every 10s | Startup errors: check dependency install, `.env`, `config`, file permissions. Do not hide repeated errors by deleting SQLite. |
| `remote-check` succeeds, but MariaDB has no samples | Confirm `probe` actual tags, `collect.log` valid points, `status` saved/queued, `sync.log` successful batch. |
| Laptop sleeps after lid close | Fix lid policy and sleep/hibernation; services cannot run while Windows is sleeping. |
| No rows during downtime when laptop was off | Expected; source did not collect those values. Already saved local rows remain queued. |

**Limitations:** The scripts are generated and statically checked on a non-Windows runtime. You must verify real Windows NSSM installation, live PLC reads, remote MariaDB and restart behavior on your laptop. The Python pipeline's existing automated tests are retained; the installed NSSM executable is not bundled here.
