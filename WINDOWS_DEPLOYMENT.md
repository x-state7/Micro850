# Windows 10/11 deployment — Micro850 → SQLite → MariaDB (no Docker)

This guide belongs to the **Windows-ready updated ZIP**, not the earlier Linux-only ZIP.

**Operating model:** one continuous Python collector reads PLC `192.168.2.8:44818` and commits successful tag values to `data/micro850_edge.sqlite3`. A second continuous Python synchronizer uploads pending local rows in ascending `edge_id` order to the `ptechview_machine.micro850_plc_samples` MariaDB table at `65.20.79.196:3306`. Both processes must be running; their internal polling/retry timers mean **do not schedule a new Python invocation every minute**.

## 1. Install and wire the laptop

- Use Windows 10 or 11 and install Python 3.11 x64 from the official Python site. The pinned `pycomm3` version historically documented testing on Windows and Python versions through 3.10; test your installed Python 3.11 runtime with the real PLC before unattended use. If compatibility problems occur, diagnose version support separately rather than changing PLC settings blindly.
- Connect the laptop's **Ethernet** port to the PLC/switch. If the PLC LAN uses `192.168.2.0/24`, configure the laptop Ethernet to an unused address such as **192.168.2.10**, subnet mask `255.255.255.0` / prefix 24. *Only use this if 192.168.2.10 is confirmed free and the subnet is indeed /24*. Normally leave the Ethernet **default gateway blank** and use the laptop's Wi-Fi/other uplink for Internet access.
- PowerShell (not Command Prompt):

```powershell
Test-NetConnection 192.168.2.8 -Port 44818
Test-NetConnection 65.20.79.196 -Port 3306
```

Both should show `TcpTestSucceeded : True` for a live setup. A failed PLC TCP check means fix cabling, network addressing, routing, controller access or firmware before running Python. A failed MariaDB check does not prevent local SQLite collection; check approved VPN, allowed IP, firewall and server reachability. An ICMP ping timeout alone does not prove a TCP failure.

## 2. Install project in fixed local folder

Extract the updated ZIP. Put the **contents** of `Micro850_SQLite_MariaDB_NoDocker` into `C:\Micro850`, so `C:\Micro850\plc_sync`, `C:\Micro850\windows`, `C:\Micro850\config`, `C:\Micro850\sql`, `C:\Micro850\requirements.txt` exist. Avoid OneDrive/cloud-sync folders and removable/network drives: SQLite's WAL needs reliable local storage.

Open PowerShell in `C:\Micro850`:

```powershell
cd C:\Micro850
py -3.11 --version
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

If `py -3.11` is unavailable, install Python 3.11 x64 then reopen PowerShell. No environment activation or Docker is required. Local test suite does not contact the real PLC or remote database.

## 3. Configure `.env` and tag mapping

```powershell
Copy-Item .env.example .env
notepad .env
notepad config\settings.json
```

Set `MARIADB_USER`, `MARIADB_PASSWORD`, `MARIADB_HOST`, `MARIADB_DATABASE` in `.env`. **Do not use the remote MariaDB root account for continuous ingestion.** Obtain or create the narrowly permissioned `plc_ingest` account. For an Internet-exposed DB use VPN or verified TLS; the example `MARIADB_TLS=off` is insecure over the public Internet. `.env` is plaintext: restrict its Windows NTFS permissions to the Windows task account and trusted administrators. Keep it out of screenshots, Git, chat, and support logs.

`config\settings.json` already has PLC host `192.168.2.8`, 10-second samples, an allowlist of candidate names from `config\tags.json`, and the local SQLite path. The Excel names must match **actual controller-exposed readable tags**; no Modbus addresses are automatically inferred. To commission with a few tags first, set `include_patterns` e.g. `['P000','P001']` using **JSON double quotes**: `"include_patterns": ["P000", "P001"]`. Only use known live tags. After verified success, expand the list. The default includes all Excel candidates and reads only successful scalar results.

Create the server table ONCE with `sql\create_table.sql` using an administrative MariaDB client on the server (or a restricted SQL admin connection). The Python account needs `INSERT` and `SELECT` on `micro850_plc_samples`; the table is uniquely keyed on `(device_id, edge_id)`. Creating the table is not performed automatically by the Windows tasks. See `README.md` for the SQL and account setup details.

## 4. Test every layer manually before scheduling

In a **PowerShell window** from `C:\Micro850`:

```powershell
.\.venv\Scripts\python.exe -m plc_sync tags
.\.venv\Scripts\python.exe -m plc_sync doctor
.\.venv\Scripts\python.exe -m plc_sync --doctor-live doctor
.\.venv\Scripts\python.exe -m plc_sync --debug --trace-tags probe
.\.venv\Scripts\python.exe -m plc_sync remote-check
.\.venv\Scripts\python.exe -m plc_sync collect-once
.\.venv\Scripts\python.exe -m plc_sync status
.\.venv\Scripts\python.exe -m plc_sync sync-once
.\.venv\Scripts\python.exe -m plc_sync status
.\.venv\Scripts\python.exe -m plc_sync remote-check
```

`probe` does **not** store values or change PLC state. `collect-once` saves a row only if at least one configured real tag is successfully read. `sync-once` uploads oldest pending rows. Expected status includes `saved`, `queued`, `synced`, `oldest_pending_id`. **Do not proceed to scheduling unless the PLC probe saves real values and the MariaDB login/table work (or consciously accept that only local collection can operate while the server is unavailable).**

For detailed values during commissioning, run `.\.venv\Scripts\python.exe -m plc_sync --debug --trace-tags --show-values probe`;  this records actual process values to `logs/probe.log`, so use only on authorized devices and treat logs as sensitive.

## 5. Run both persistent processes manually

In PowerShell **terminal A**:

```powershell
cd C:\Micro850
.\windows\run_collector.cmd
```

In PowerShell **terminal B**:

```powershell
cd C:\Micro850
.\windows\run_sync.cmd
```

In **terminal C**:

```powershell
cd C:\Micro850
.\.venv\Scripts\python.exe -m plc_sync status
Get-Content .\logs\collect.log -Tail 30 -Wait
```

Use another terminal for `Get-Content .\logs\sync.log -Tail 30 -Wait`. The launch scripts each supervise a long-lived Python command and restart it after 10 seconds on an unexpected exit. Fatal exit code **42** intentionally stops to prevent a remote duplicate with a different payload. Both workers use OS-level single-instance locks; never intentionally run duplicate collectors or synchronizers.

**Before Task Scheduler starts them, stop both manual processes** (Ctrl+C in each terminal). Use Task Scheduler only after the manual test has succeeded.

## 6. Recommended hands-on Task Scheduler installation (normal Windows account)

1. Run `taskschd.msc` (Start → search **Task Scheduler**).
2. Click **Create Task** (not Create Basic Task). Name `Micro850-Collector`.
3. General: select **Run whether user is logged on or not**; use a Windows account that has access to `C:\Micro850`, including `.env`, `data`, and `logs`. Windows may ask for that account's password. You may select **Run with highest privileges** *only if required by folder permissions*; least privilege is preferable.
4. Triggers → New → **Begin the task: At startup** → Enabled.
5. Actions → New → **Start a program**. Program: `C:\Windows\System32\cmd.exe`; arguments: `/d /c ""C:\Micro850\windows\run_collector.cmd""`; Start in: `C:\Micro850` (no quotes in this field).
6. Conditions: **uncheck** `Start the task only if the computer is idle`; **uncheck** `Start only if the following network connection is available` (collector must buffer offline); decide battery policy, but use AC power for nonstop operation.
7. Settings: **uncheck** `Stop the task if it runs longer than: 3 days`; enable `Allow task to be run on demand`, `Run task as soon as possible after a scheduled start is missed`, and `If the task fails, restart every 1 minute`, 3 times. For `If the task is already running`, select **Do not start a new instance**.
8. Save. Create **Micro850-Synchronizer** with exactly the same settings except the Action arguments are `/d /c ""C:\Micro850\windows\run_sync.cmd""`.
9. Right-click each task → **Run**. After 15–30 seconds, verify their state is `Running`, and inspect `logs\collect.log` and `logs\sync.log`. The task processes run in the background, so a visible terminal is not expected.

Task Scheduler's restart count is finite (Windows supports at most 255 in its restart schema). The **batch launcher provides continuous restart attempts** for nonfatal process exits, while Task Scheduler handles startup and occasional launcher failures. The Python collector and uploader also include their own PLC and MariaDB retry loops.

## 7. Alternative: automatic registration with PowerShell (LocalSystem)

For a **quick unattended boot setup**, the included installer registers the tasks as Windows **LocalSystem**, a highly privileged built-in account. **Use only on a dedicated, trusted laptop, after reviewing the script, and prefer a restricted service user via Task Scheduler GUI for production.** LocalSystem has access to local folders; it does not need an interactive login.

Open **Windows PowerShell as Administrator**:

```powershell
cd C:\Micro850
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\windows\install_windows_tasks.ps1
Start-ScheduledTask -TaskName 'Micro850-Collector'
Start-ScheduledTask -TaskName 'Micro850-Synchronizer'
Get-ScheduledTask -TaskName 'Micro850-Collector','Micro850-Synchronizer' | Select-Object TaskName,State
```

Re-running the installer **overwrites tasks of the same name**, so do not use it after configuring a custom task identity or permissions manually. To remove scheduled tasks without deleting PLC or SQLite data:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\windows\uninstall_windows_tasks.ps1
```

## 8. Configure Windows power plan for 24/7 use

Plug the laptop into reliable AC power. In Windows **Settings → System → Power & battery**, set **Sleep: Never while plugged in**. For most laptops also review Control Panel → Power Options → **Choose what closing the lid does**, and do not close the lid unless configured to stay running and thermals are safe.

PowerShell (administrative privileges may be needed):

```powershell
powercfg /change standby-timeout-ac 0
powercfg /change hibernate-timeout-ac 0
```

Let the display turn off to save energy; display power-off is not PC sleep. Do not disable security updates forever. **Shutting down, rebooting, sleeping or powering off the laptop stops live sampling**; only already-saved SQLite rows can later be uploaded. Automatic boot startup cannot retroactively recover values the PLC never sent while the collector was off.

## 9. Acceptance tests

**A. Confirm service run:** Both scheduled tasks should remain in `Running` state; `Get-Content .\logs\collect.log -Tail 30` should show `SQLITE_SAVED`, and `logs\sync.log` should show `SYNC_BATCH_COMPLETE` when backlog exists.

```powershell
cd C:\Micro850
Get-ScheduledTask -TaskName 'Micro850-Collector','Micro850-Synchronizer' | Select-Object TaskName,State
Get-ScheduledTaskInfo -TaskName Micro850-Collector
Get-ScheduledTaskInfo -TaskName Micro850-Synchronizer
.\.venv\Scripts\python.exe -m plc_sync status
Get-Content .\logs\collect.log -Tail 30
Get-Content .\logs\sync.log -Tail 30
```

**B. Offline test:** Temporarily disconnect **only Internet/Wi-Fi** while Ethernet to the PLC stays alive. Collect status should show `saved` and `queued` increasing. Restore Internet; `queued` should drain to 0. Check remote database sample count and ID range. Avoid doing this during safety-critical production operations.

**C. Restart test:** Reboot the laptop when authorized; log in and check both tasks in Task Scheduler, inspect the log timestamps, and verify new `edge_id` samples. They should already have started after boot; no manual PowerShell window required.

**D. Duplicate/ordering test:** In MariaDB run:

```sql
SELECT device_id,edge_id,COUNT(*) AS copies
FROM ptechview_machine.micro850_plc_samples
GROUP BY device_id,edge_id
HAVING COUNT(*) > 1;

SELECT edge_id,sampled_at_utc,point_count,LEFT(points_json,200) AS example_values
FROM ptechview_machine.micro850_plc_samples
WHERE device_id='micro850-192-168-2-8'
ORDER BY edge_id DESC LIMIT 10;
```

The first query should return no rows. The second shows the latest samples.

## 10. Troubleshooting

| What you see | Next step |
|---|---|
| `No module named fcntl` | You are still running the **old Linux-only ZIP**; use this Windows-ready ZIP. |
| `py -3.11 not found` | Install Python 3.11 x64; ensure `py -3.11 --version` works. |
| `TcpTestSucceeded : False` for 44818 | Check Ethernet adapter IP/subnet and PLC/switch/CIP access. |
| `CIP_TAG_NOT_FOUND` | Confirm the actual CCW controller tag names and visibility; restrict `include_patterns`. |
| `SQLITE_NO_SAMPLE` | No valid live tag values arrived; probe PLC reads. |
| MariaDB `1045 Access denied` | Fix `plc_ingest` password, allowed public source IP and grants. |
| MariaDB `1146` missing table | Apply `sql\create_table.sql` as admin; verify DB and table name. |
| `SYNC_RETRY` | Internet/DB temporarily unavailable; local queue stays intact. |
| `SYNC_HALT_DATA_CONFLICT`, exit `42` | **Do not reset SQLite or delete server rows**. Investigate the device ID, sample ID and payload digest mismatch. |
| Task exits after 3 days | Disable **Stop the task if it runs longer than**; reinstall tasks with provided script. |
| Task says Ready, not Running | Check account/password, Run history, action path, `.env` permission, and `logs\windows_*_supervisor.log`. |
| No rows after restart | Look for Wi-Fi/PLC adapter reconnection, tag access, Windows sleep, local data/log permissions. |

**Data durability:** never erase `data\micro850_edge.sqlite3`, its `-wal`/`-shm` companion files, or modify `device_id` to silence an upload conflict. Preserve logs and back up SQLite safely with `python tools\backup_sqlite.py --help` or follow the backup procedure in `README.md`.
