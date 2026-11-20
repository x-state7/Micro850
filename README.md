# Rockwell Micro850 → SQLite → MariaDB, without Docker

> **Windows 10/11 setup:** Use **`WINDOWS_DEPLOYMENT.md`** and the `windows/` launch/Task Scheduler scripts. The collector/sync single-instance locks now work on Windows (`msvcrt`) and Linux (`fcntl`). **Do not use Linux `systemd` instructions on a Windows laptop.**

**Ready-to-run source code. Hardware/database connectivity must be verified at the installation site.**

This is an adaptation of the uploaded PLC tank farm project. It removes the Docker simulator, central Python HTTP receiver and central SQLite server, while retaining the durable local queue and idempotent retry design. The default connection reads **Rockwell Micro850 EtherNet/IP (CIP) tags directly** using `pycomm3.LogixDriver`, not simulated Modbus addresses. No write operations to the PLC are implemented.


## Live terminal troubleshooting (new)

Every Python command now writes timestamped INFO/WARNING/ERROR messages to the terminal and detailed DEBUG logs to `logs/<command>.log` (5 MiB per file + 3 rotated copies). `python -m plc_sync doctor` checks the network, tag configuration, SQLite integrity, disk space, MariaDB socket and service-account configuration without writing data. `python -m plc_sync --doctor-live doctor` additionally tests actual PLC tag reads and a remote authenticated read-only query.

For complete terminal diagnostics, use:

```bash
python -m plc_sync doctor
python -m plc_sync --debug --trace-tags --show-values probe
python -m plc_sync --debug collect       # Terminal 1
python -m plc_sync --debug sync          # Terminal 2
python -m plc_sync status               # Terminal 3
```

**`--trace-tags --show-values` prints every tag and its actual value; enable only when needed because the project can read hundreds of tags.** Values and tag names may be sensitive. `--debug` shows detailed protocol, SQL batch, queue, retry and per-error information without exposing the password. Per-tag values are not included in background logs by default. For error codes, a sample debugging session and offline-to-online validation, read **[DEBUGGING_GUIDE.md](DEBUGGING_GUIDE.md)**.

## 1. Actual connection parameters

| Setting | Value |
|---|---|
| PLC make/model | Rockwell Automation Micro850 (Micro800 family) |
| PLC IP | `192.168.2.8` |
| PLC port/protocol | EtherNet/IP TCP **44818** (default) |
| Edge storage | `data/micro850_edge.sqlite3` (SQLite) |
| MariaDB server | `65.20.79.196:3306` |
| MariaDB database | `ptechview_machine` |
| MariaDB table | `micro850_plc_samples` |
| Data source | The **successfully read live PLC tags**, not Excel project values |
| Deployment | Two manual Python processes or two lightweight systemd services |

`reference/sql.xlsx` is the original Connected Components Workbench variable export. `config/tags.json` is a list of **528 individually addressable candidate tag names** derived from it. The source spreadsheet has **529 data/array rows**, including the parent `DATALOG` array declaration and its 20 individual elements. The tag file excludes the parent declaration and has the 20 separate elements (`DATALOG[1]` ... `DATALOG[20]`). Its values are not imported; every reading must come from the live PLC.

**Important caveat:** a variable appearing in `sql.xlsx` does **not** prove that it is present in the running PLC program or remotely accessible. The collector intersects the Excel tag names with the list returned by the PLC and saves only successful reads. A working network, the correct PLC program and readable external tag access are prerequisites. No PLC connection was attempted from the development environment.

## 2. Data flow / offline logic

```text
Micro850 192.168.2.8
   │  read-only EtherNet/IP / CIP (TCP 44818)
   ▼
Collector: python -m plc_sync collect
   │  each poll: save only successfully read scalar tags
   ▼
SQLite: data/micro850_edge.sqlite3
   │  sample 1  sample 2  sample 3  sample 4 ...
   │                 synced_at_utc NULL = pending
   ▼
Synchronizer: python -m plc_sync sync
   │  earliest pending first, small batches
   │  if MariaDB unavailable: no local ACK; retry later
   ▼
MariaDB: 65.20.79.196 / ptechview_machine
   └─ micro850_plc_samples (UNIQUE device_id,edge_id)
```

A local row is committed to SQLite **before** any cloud interaction. The independent synchronizer uploads the lowest pending `edge_id` first. After the complete MariaDB transaction commits, it marks those local rows `synced`. If the network goes offline, samples continue accumulating. When it returns, the synchronizer resumes from the oldest pending ID. If a remote commit succeeded but the acknowledgment was lost, the duplicate `(device_id,edge_id)` is safely verified using a SHA-256 payload digest and acknowledged rather than reinserted.

A sample is a JSON object with only the available points. `0`, `0.0`, and `false` are **real data** and are not dropped. Unreadable tags, nulls, NaN/Inf and empty scans do not get fabricated values. A constant, readable tag is recorded on **each poll** (this is sampling, not event/change-only logging). Partial successful scans retain successful tag values; an exception that aborts a scan does not insert the incomplete sample.

> Safety: This is passive telemetry and is **not** a safety or control system. Do not use it for emergency shutdown, interlocking, machine control, or guaranteed sub-second capture. The program does not reconstruct PLC values missed during a PLC/LAN outage; it can only preserve samples already captured locally.

## 3. Ubuntu/Linux step-by-step installation

Run on the **edge computer physically able to reach the PLC**. This can be a small always-on Linux mini PC, existing Ubuntu PC, or industrial PC. If the computer is powered down, it cannot poll the PLC. A server somewhere on the Internet normally cannot directly connect to private `192.168.2.8` without an approved route/VPN.

### A. Extract and prepare Python

```bash
unzip Micro850_SQLite_MariaDB_NoDocker.zip
cd Micro850_SQLite_MariaDB_NoDocker
sudo apt update
sudo apt install -y python3 python3-venv python3-pip mariadb-client
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

The reader uses `pycomm3`; the database uploader uses pure-Python `PyMySQL`; `python-dotenv` reads the local `.env`. SQLite, logging, and the optional Modbus TCP client are Python standard library components. No Docker, database daemon, HTTP receiver, or simulator runs on the edge.

### B. Confirm local PLC network connectivity

```bash
ip -br addr                       # Ethernet adapter must be routed to 192.168.2.8
ip route get 192.168.2.8
ping -c 4 192.168.2.8            # ICMP may be disabled
nc -vz -w 3 192.168.2.8 44818   # EtherNet/IP/CIP
```

An example setup is edge Ethernet `192.168.2.50/24`, PLC `192.168.2.8/24`, and separate Wi-Fi/second network interface for Internet access. **Only use these edge NIC values if approved by the plant network administrator and they do not conflict with any existing device.** Do not change live PLC addressing to make the software work.

### C. Confirm correct live tags (no DB writes)

```bash
python -m plc_sync --config config/settings.json tags
python -m plc_sync --config config/settings.json probe
```

`probe` reads the PLC **without saving** data to SQLite or MariaDB. It prints actual successful values, number of missing/unreadable tags, and errors. If you receive no available tags, check TCP 44818, PLC program availability, CCW project/tag scope, and the controller's Ethernet/IP access. Ask the PLC engineer to verify the tags are accessible externally.

To reduce load and target known inputs, edit `config/settings.json`:

```json
"include_patterns": ["Data*", "DATALOG*", "P00*"],
"exclude_patterns": []
```

Glob patterns match the **Excel tag names**, not measured values. Start with a small subset for commissioning, then expand to `"include_patterns": ["*"]` to attempt all 528 candidates. The default `"batch_size": 20` can be set to `1` if the PLC or firmware fails on multi-tag requests. Adjust `"sample_interval_seconds": 10` according to PLC load and business requirements.

### D. Create the destination table in MariaDB

From a machine with access to your MariaDB administrator account:

```bash
mariadb --ssl=OFF -h 65.20.79.196 -u root -p ptechview_machine < sql/create_table.sql
```

The command above follows your supplied command syntax, but **`--ssl=OFF` transmits database traffic without TLS and is dangerous across a public network**. Prefer a VPN/private tunnel or verified TLS. Use the root account **only** for one-time administration, never as the continuously running Python application's user.

Then create a limited service account in the MariaDB console (replace the placeholder host with your edge machine's **public source IP as seen by MariaDB**, not `192.168.2.8`):

```sql
CREATE USER 'plc_ingest'@'<EDGE_PUBLIC_IP>' IDENTIFIED BY '<LONG_RANDOM_PASSWORD>';
GRANT SELECT, INSERT ON ptechview_machine.micro850_plc_samples
  TO 'plc_ingest'@'<EDGE_PUBLIC_IP>';
```

Ensure the MariaDB firewall allows TCP 3306 **only** from your approved VPN/source IP, not the whole Internet. If your edge's public IP changes, prefer a VPN to opening access to `%`. Confirm the table exists:

```sql
USE ptechview_machine;
SHOW TABLES LIKE 'micro850_plc_samples';
SHOW CREATE TABLE micro850_plc_samples\G
```

### E. Configure the service account and check remote connectivity

```bash
cp .env.example .env
nano .env
chmod 600 .env
python -m plc_sync --config config/settings.json remote-check
```

Enter the service account's actual `MARIADB_USER` and `MARIADB_PASSWORD` in `.env`. Do not paste a password into shell arguments, source code, Git, or chat. Keep `MARIADB_DATABASE=ptechview_machine`. With a verified CA certificate, set `MARIADB_TLS=verify` and `MARIADB_SSL_CA=/path/to/trusted-ca.pem` (certificate must be valid for the endpoint hostname/IP). Default `off` matches the user's CLI only, **not** a production security recommendation.

If SSH access to the MariaDB host exists, an alternative to exposing TCP 3306 is a private local tunnel (using a separately authorized SSH user):

```bash
ssh -N -L 3307:127.0.0.1:3306 YOUR_SSH_USER@65.20.79.196
```

Then set `MARIADB_HOST=127.0.0.1` and `MARIADB_PORT=3307` in `.env`. The SSH channel encrypts the connection even though the inner MySQL protocol sets TLS off. Keep the SSH tunnel running (or manage it as a service) for continuous synchronization. This only works if SSH access and server-side MariaDB loopback connectivity are permitted.

If the database cannot be reached, **the collector still works**. Only the sync process needs MariaDB credentials or Internet connectivity.

### F. Test one sample and one sync batch

```bash
python -m plc_sync --config config/settings.json collect-once
python -m plc_sync --config config/settings.json status
python -m plc_sync --config config/settings.json sync-once
python -m plc_sync --config config/settings.json status
python -m plc_sync --config config/settings.json remote-check
```

Check the MariaDB data:

```sql
SELECT id,device_id,edge_id,sampled_at_utc,point_count,
       JSON_EXTRACT(points_json, '$.Data1') AS data1,
       JSON_EXTRACT(points_json, '$."DATALOG[1]"') AS datalog_1
FROM ptechview_machine.micro850_plc_samples
ORDER BY id DESC LIMIT 10;
```

If the Excel variables are not present on the actual controller, there will be no new local sample until the PLC/CCW tags are resolved. **Do not substitute spreadsheet Project Value cells as readings.**

### G. Run manually: TWO terminals

**Terminal 1 — collector (always saves locally)**

```bash
cd Micro850_SQLite_MariaDB_NoDocker
source .venv/bin/activate
python -m plc_sync --config config/settings.json collect
```

**Terminal 2 — uploader (retries and backfills automatically)**

```bash
cd Micro850_SQLite_MariaDB_NoDocker
source .venv/bin/activate
python -m plc_sync --config config/settings.json sync
```

**Terminal 3 — watch local stats**

```bash
cd Micro850_SQLite_MariaDB_NoDocker
source .venv/bin/activate
python -m plc_sync --config config/settings.json status
```

`queued` rises if the remote server is offline. When connectivity is restored, `queued` falls toward zero and `synced` rises. The local SQLite file is intentionally retained even after synchronization; do not delete it if you want continuous source IDs.

### H. Make both Python programs run at boot (recommended)

This is **still Docker-free**. `systemd` starts and restarts the two plain Python processes automatically. Put the project at `/opt/micro850-pipeline` and ensure it is owned by the account that will run the service:

```bash
sudo mkdir -p /opt/micro850-pipeline
# Copy the project (including its .venv and your protected .env) to this folder.
# It is preferable to recreate .venv in this directory after copying.
cd /opt/micro850-pipeline
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
sudo chown -R "$USER":"$USER" /opt/micro850-pipeline
```

Edit `systemd/micro850-collector.service` and `systemd/micro850-sync.service` to replace `YOUR_LINUX_USERNAME` with `whoami` output; ensure `WorkingDirectory` and `ExecStart` paths point to the installed project. Then:

```bash
sudo cp systemd/micro850-collector.service /etc/systemd/system/
sudo cp systemd/micro850-sync.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now micro850-collector micro850-sync
sudo systemctl status micro850-collector micro850-sync --no-pager
sudo journalctl -u micro850-collector -u micro850-sync -f
```

The collector and sync processes have separate file locks; restarting a service cannot accidentally create a duplicate instance for the same SQLite file.

## 4. Offline → online acceptance test

1. Start both services and verify `queued=0` once up to date.
2. Temporarily block **only** the MariaDB route/network (do not disrupt the PLC Ethernet LAN or change its program).
3. Observe that the collector logs `SQLite saved sample...`; `status` shows `queued` growing.
4. Restore MariaDB connectivity without clearing SQLite.
5. Observe the uploader drain the queue **oldest edge_id first**.
6. Confirm there is at most one remote record for each `(device_id,edge_id)`:

```sql
SELECT device_id,edge_id,COUNT(*) AS n
FROM ptechview_machine.micro850_plc_samples
GROUP BY device_id,edge_id
HAVING n > 1;
```

7. Check that remote sample count matches local `synced` for a new, non-reused `device_id`, assuming there were no older rows or intentional retention changes. The local database is **the authoritative upload queue**; never remove pending rows to fix a sync problem.

Run the complete offline/ack-loss tests without a PLC or MariaDB server:

```bash
python -m unittest discover -s tests -v
```

## 5. What if the PLC only exposes Modbus TCP?

The source includes the original dependency-free, read-only Modbus client, but the **provided Excel is a symbolic tag export, not a Modbus Mapping export**. You cannot derive Modbus register addresses or word order from those columns. If the PLC engineer exports the **Modbus Mapping** from CCW, set `"protocol": "modbus_tcp"` in `config/settings.json` and fill in the `"modbus": { "points": [...] }` with the actual mapped registers. Do not use the legacy simulator's tank/pump registers in production.

On several Micro850 CCW versions Modbus TCP server operation must be enabled and its mapping explicitly configured. EtherNet/IP is the primary mode here because it reads symbolic tags by name without this mapping. Both are read-only in this codebase.

An example placeholder (not live mapping):

```json
"modbus": {
  "port": 502,
  "unit_id": 1,
  "timeout_seconds": 3,
  "points": [
    {"name": "EXAMPLE_ONLY", "table": "hr", "address": 0,
     "type": "float32", "word_order": "ABCD"}
  ]
}
```

The Modbus address in the example is **not verified** for your PLC and must be replaced with the real CCW mapping before use.

## 6. Regenerate tag list when the CCW export changes

`tools/export_ccw_tags.py` uses only Python standard library (no Excel package required on the edge):

```bash
python tools/export_ccw_tags.py reference/sql.xlsx config/tags.json
python -m plc_sync --config config/settings.json tags
```

Stop the collector first, regenerate the list, then restart the collector. Use the same `device_id` and local database if it is the same physical PLC installation; never reset the SQLite edge IDs if the server has already received them. If you change to a genuinely new device/installation, choose a different `device_id`.

## 7. Local queries and backups

Read the SQLite file directly (when `sqlite3` CLI is installed):

```bash
sqlite3 -readonly data/micro850_edge.sqlite3 \
  'SELECT edge_id,sampled_at_utc,point_count,synced_at_utc FROM samples ORDER BY edge_id DESC LIMIT 10;'
```

For a safe live SQLite backup, use Python's backup API rather than copying only the `.sqlite3` file while WAL mode is active:

```bash
python tools/backup_sqlite.py data/micro850_edge.sqlite3 backup/micro850_edge_backup.sqlite3
```

The application does **not** automatically delete synchronized data. Plan disk size and retention as needed; preserve an archived backup and never prune pending records. Back up the MariaDB database separately. If the edge disk fills, local storage cannot accept more samples.

## 8. Troubleshooting

| Problem | Resolution |
|---|---|
| PLC TCP 44818 unreachable | Check routing/NIC/VLAN/firewall, actual device IP, and Ethernet/IP service |
| `No Excel variables match available live PLC tags` | Verify correct controller program, remote-visible tag names, scopes and permissions in CCW |
| PLC opens but many tags show errors | Begin with `Data*`, verify tag external access, lower `batch_size` to `1` |
| No new local samples | Probe may have zero successful values; inspect failures rather than storing guessed zeros |
| `queued` keeps growing | Check network to MariaDB, correct `.env`, server firewall and target table |
| MariaDB `Access denied` | Create correct `plc_ingest` account scoped to edge source IP; grant `INSERT, SELECT` |
| Duplicate mismatch | `device_id` reused with a different local DB. Stop and investigate; never overwrite original rows |
| PLC loses power/network | No samples can be recovered for intervals in which the edge did not read the controller |
| Sync process stops | Restart `python -m plc_sync sync`; retained unsynced rows replay safely |
| System time wrong | Enable NTP; timestamps are UTC and represent edge poll time |

## 9. Files

```text
plc_sync/
  __main__.py      # CLI for doctor/probe/collect/sync/status
  plc_reader.py    # Micro850 CIP tag discovery + read-only reads
  collector.py     # periodic PLC samples -> SQLite
  storage.py       # SQLite WAL, monotonic edge IDs and pending queue
  synchronizer.py  # direct MariaDB transactions, digest-backed replay
  modbus.py        # original legacy read-only Modbus TCP client
  modbus_points.py # original legacy Modbus point decoding
  locks.py         # one collector and one sync process per DB
  diagnostics.py   # read-only network, tag, DB and disk health checks
  error_hints.py   # error classification and actionable resolution hints
  log_setup.py     # rolling DEBUG logs + terminal logs + secret redaction
config/
  settings.json    # real PLC IP, sampling intervals, tag selection
  tags.json        # 528 tag names generated from the actual Excel
sql/create_table.sql
reference/sql.xlsx
tools/export_ccw_tags.py
tools/backup_sqlite.py
systemd/
  micro850-collector.service
  micro850-sync.service
tests/test_pipeline.py
tests/test_debugging.py
DEBUGGING_GUIDE.md
.env.example
requirements.txt
```

## Verification scope

Automated tests cover local storage, partial/missing tags, offline backlog, FIFO upload, duplicate conflict, lost MariaDB commit acknowledgment, and lost local acknowledgment. These tests **do not** prove that the actual plant PLC is reachable, that its program exposes all tags, or that the remote public MariaDB endpoint accepts your account. Verify `probe`, `remote-check`, and a small controlled live collection on-site before enabling continuous production telemetry.


## Windows 24/7 without Task Scheduler

See `windows_service/WINDOWS_SERVICE_24X7_GUIDE.md` and its `install_services.cmd`. It uses NSSM to manage two Python Windows services without activating `.venv`.
