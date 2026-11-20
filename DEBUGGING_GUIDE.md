# Micro850 PLC Pipeline — Live Terminal Debugging Guide

This guide applies to the **no-Docker Python** project. It does not change the PLC program, write PLC values, delete SQLite samples or require a test MariaDB server. Everything is logged with a timestamp, severity, component and an event name.

## 1. Start with a read-only diagnostic

From the extracted project directory on the **edge machine connected to the PLC**:

```bash
source .venv/bin/activate
python -m plc_sync doctor
```

`doctor` checks the 528-tag candidate configuration, TCP reachability of the PLC at `192.168.2.8:44818`, SQLite integrity and queue if present, free disk space, MariaDB TCP reachability at `65.20.79.196:3306`, and whether a non-placeholder password is configured. It **does not authenticate to MariaDB or read PLC tags** unless specifically requested. It reports each `DIAG_PASS`/`DIAG_FAIL` and a suggested next step, then prints JSON. Exit code is `0` for all checks passing or `2` for failed checks. An unreachable remote database is an expected failure while offline.

To **also** test actual PLC tag reads and authenticated MariaDB `SELECT` queries (no PLC or database writes):

```bash
python -m plc_sync --doctor-live doctor
```

Check your edge machine network interfaces separately:

```bash
ip -br addr
ip route get 192.168.2.8
nc -vz -w 3 192.168.2.8 44818
nc -vz -w 3 65.20.79.196 3306
```

A green TCP port alone does not guarantee tags are externally accessible or the MariaDB account has permissions. `doctor --doctor-live`, `probe`, and `remote-check` test those details.

## 2. Inspect tag access before writing anything

**Normal mode (actual successful values and first 15 errors printed):**

```bash
python -m plc_sync probe
```

**Maximum readable detail, including every attempted tag and its actual value:**

```bash
python -m plc_sync --debug --trace-tags --show-values probe
```

`--trace-tags` prints one success/error entry per tag in each read cycle. `--show-values` explicitly permits PLC values to enter log files and the terminal. **Do not upload these logs publicly** if tag names or measured values are sensitive. Avoid leaving these options on permanently for 528 tags.

If most reads fail, first change `include_patterns` in `config/settings.json` to a small set of **confirmed external-access tags** (for example `Data1`, if it actually exists) and retry. Do not manufacture values for unknown tags or copy initial values out of Excel. If the PLC/CCW firmware rejects batch reads, set `plc.batch_size` to `1` and try again.

## 3. Real-time collector and uploader logging

**Terminal A — collect real PLC readings into SQLite:**

```bash
python -m plc_sync --debug collect
```

The terminal reports `PLC_CONNECTING`, `CIP_CONNECTED`, `POLL_START`, `PLC_READ_START`, `PLC_READ_END`, `SQLITE_SAVED edge_id=... valid_points=...`, `PLC_TAG_ERRORS` (if any), `POLL_END` with duration and a periodic `COLLECTOR_HEARTBEAT` showing pending and uploaded counts. No row is created if there are zero successful live tags. `--debug` prints additional batch and error details.

**Terminal B — forward pending rows to MariaDB:**

```bash
python -m plc_sync --debug sync
```

You will see `SYNC_BATCH_START` with the exact first/last `edge_id`, `MARIADB_CONNECTING`/`MARIADB_CONNECTED`, `MARIADB_INSERT_ATTEMPT`, `MARIADB_COMMIT_BEGIN`, `MARIADB_COMMIT_CONFIRMED`, `SYNC_BATCH_COMPLETE`, and `SYNC_QUEUE_STATUS`. If no work is pending, `SYNC_IDLE_HEARTBEAT` reports health periodically.

**Terminal C — inspect queue without stopping either process:**

```bash
python -m plc_sync status
```

`queued` = rows safely retained on the edge waiting to be acknowledged by MariaDB; `synced` = rows whose remote transaction was committed and then marked locally. `oldest_pending_id` identifies where ordered replay will resume.

Use `--trace-tags --show-values collect` if you specifically need a **terminal line per individual tag per poll**. It will create substantial logging volume if 528 tags are active. The default prints a per-poll summary and a small list of tag names to limit output.

## 4. See the files even after terminal closes

Every command writes a separate rolling log at `logs/<command>.log`, by default even when terminal output is at INFO level. Log files capture **DEBUG** details and rotate after 5 MiB, retaining three older copies for each process (approximately 20 MiB per active command). Log files are created with owner-only permissions where supported. Files that include operational data should be protected.

```bash
tail -n 100 -f logs/collect.log
# or
# tail -n 100 -f logs/sync.log

grep -nE 'ERROR|WARNING|FAILED|RETRY|DATA_CONFLICT' logs/sync.log

grep -nE 'CIP_TAG_FAILED|PLC_TAG_ERRORS|SQLITE_SAVED|PLC_RECOVERED' logs/collect.log
```

Customize output with `.env`:

```dotenv
LOG_LEVEL=INFO
MICRO850_LOG_DIR=logs
```

Use `LOG_LEVEL=DEBUG` for verbose terminal output, or `--debug` for one command. `--no-log-file` disables file logs if storage is very limited. `--show-values` is off by default. **Credentials are never intentionally logged**, and the logger redacts configured MariaDB passwords in standard messages/tracebacks; do not paste `.env` or raw authentication diagnostics into issue reports.

To limit the debug output during extended operation, run `collect` and `sync` without `--debug`; console still displays all important per-poll and per-batch events, while the rolling file keeps DEBUG messages.

## 5. Common event codes / what to do

| Terminal event or hint | What it means | Next action |
|---|---|---|
| `PLC_TCP_ERROR`, `PLC_NETWORK_ERROR` | PLC socket failed or timed out | Check PLC power, edge NIC, `ip route`, VLAN, TCP 44818 |
| `PLC_TAG_MAPPING`, `CIP_TAG_NOT_FOUND` | Excel candidate not found in actual PLC | Check CCW program, tag spelling and remote access |
| `CIP_TAG_FAILED` | Specific tag returned error/unsupported value | `--trace-tags probe`, inspect exact tag, type and error |
| `SQLITE_NO_SAMPLE` | No successful readings | Fix PLC communication; no fake values are saved |
| `SQLITE_LOCKED` | SQLite was locked | Check for second collector / other DB tools holding transaction |
| `SQLITE_PERMISSIONS`, `SQLITE_DISK` | Local file not writable or disk full | Fix user permissions/free space without deleting pending rows |
| `DB_TCP_UNREACHABLE`, `DB_CONNECTION_LOST` | MariaDB not reachable right now | Keep collector running, check internet/VPN/firewall; automatic retries |
| `DB_LOGIN_DENIED` | MariaDB rejected credentials or account host | Check `.env`, account grants and client public source IP |
| `DB_TABLE_MISSING` | Target table not created | Run `sql/create_table.sql` once on remote server |
| `SYNC_SAFE_REPLAY` | Remote row already exists and digest matches | Normal after lost acknowledgment; no duplicate will be inserted |
| `SYNC_DATA_CONFLICT` / `DB_DATA_CONFLICT` | Same `device_id + edge_id` with different payload | **Stop and investigate**; exit code 42 prevents automatic systemd restart; don't reset local IDs or overwrite |
| `SQLITE_ACK_FAILED` | Remote commit succeeded, but local pending ACK failed | Fix SQLite; automatic replay is safe due unique key |

An unexpected exception prints a **full Python traceback**. The preceding `ERROR_HINT` line provides a stable code and likely corrective action.

## 6. Offline-to-online recovery test

1. Run collector and sync in separate terminals and verify at least one `SQLITE_SAVED` / `SYNC_BATCH_COMPLETE` event.
2. Disconnect **only** the Internet/VPN path to MariaDB; leave the PLC's local Ethernet connected.
3. Observe `MARIADB_*FAILED`, `SYNC_RETRY ... pending=... retry_in_seconds=...` and continued `SQLITE_SAVED edge_id=N` events. `status` shows `queued` increasing.
4. Restore MariaDB connectivity. Observe `MARIADB_RECOVERED`, `SYNC_BATCH_START first_edge_id=<oldest_pending_id>` and `SYNC_QUEUE_STATUS pending=...` falling toward zero.
5. Query MariaDB to verify remote IDs are unique:

```sql
SELECT device_id, edge_id, COUNT(*) AS duplicate_count
FROM ptechview_machine.micro850_plc_samples
GROUP BY device_id, edge_id
HAVING COUNT(*) > 1;
```

This query should return zero rows; the schema's unique key also enforces this.

**Important:** No software can reconstruct PLC readings for time periods when the edge machine or PLC was off. This pipeline guarantees replay of samples that were successfully committed to local SQLite, not missing measurements that were never collected.

## 7. If processes use systemd

```bash
sudo journalctl -u micro850-collector -f -o cat
sudo journalctl -u micro850-sync -f -o cat
sudo systemctl status micro850-collector micro850-sync --no-pager
```

The two Python processes also write their own logs in `logs/` under the configured working directory. Do not run manual collector/sync in parallel with the same active systemd service: the single-instance lock prevents duplicate instances.

## 8. Run tests offline

```bash
python -m unittest discover -s tests -v
```

The test suite uses a simulated in-memory MariaDB connection and FakePLC objects **for tests only**. It tests ordered replay, lost remote ACK, lost local ACK, duplicate conflict, logging safety, per-tag tracing, doctor checks and error classification. Real controller access and remote server permissions must still be verified at the site.
