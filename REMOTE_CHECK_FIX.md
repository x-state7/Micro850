# Remote-check Decimal output hotfix (November 2026)

`remote-check` can successfully connect to MariaDB and then fail when printing its status:

`TypeError: Object of type Decimal is not JSON serializable`

MariaDB may return `decimal.Decimal` for `COUNT` or `MAX` aggregates. The fix in
`plc_sync/synchronizer.py` casts the two integral aggregate outputs to `int`.
No changes to MariaDB schema, SQLite storage or credentials are needed.

## Apply without overwriting Windows settings

You can replace only `C:\Micro850\plc_sync\synchronizer.py` with the fixed file.
Do not replace your `.env` or `data\micro850_edge.sqlite3`.

```powershell
cd C:\Micro850
.\.venv\Scripts\python.exe -m plc_sync remote-check
.\.venv\Scripts\python.exe -m plc_sync status
.\.venv\Scripts\python.exe -m plc_sync --debug probe
.\.venv\Scripts\python.exe -m plc_sync collect-once
.\.venv\Scripts\python.exe -m plc_sync sync-once
```

`remote_rows=0` means no rows for the configured `device_id`, not necessarily
that the entire server table is empty. Passwords must not be posted in logs.
