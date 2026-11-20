"""Read-only, actionable checks for PLC/SQLite/MariaDB commissioning."""
import logging
import os
import shutil
import socket
import sqlite3
from contextlib import closing
from pathlib import Path
from .config import load_tags
from .storage import status

LOG = logging.getLogger("micro850.doctor")


def tcp_check(host, port, timeout=3):
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True, "TCP connection established"
    except (OSError, ValueError) as exc:
        return False, f"{type(exc).__name__}: {exc}"


def run_doctor(cfg, *, include_remote_auth=False, include_plc_probe=False):
    """Does not write to PLC or remote DB. Emits diagnosis and JSON-safe report."""
    results = []

    def report(name, ok, detail, hint=""):
        entry = {"check": name, "ok": bool(ok), "detail": str(detail)}
        if hint and not ok:
            entry["next_step"] = hint
        results.append(entry)
        (LOG.info if ok else LOG.error)("DIAG_%s %s %s%s", "PASS" if ok else "FAIL",
                                         name, detail, f"; next={hint}" if hint and not ok else "")

    plc = cfg["plc"]
    LOG.info("DIAG_START device=%s protocol=%s host=%s mode=read_only", cfg["device_id"],
             plc["protocol"], plc["host"])
    if plc["protocol"] == "ethernetip":
        try:
            tags = load_tags(plc["tag_file"], plc.get("include_patterns", ["*"]),
                             plc.get("exclude_patterns", []))
            report("tag_list", True, f"{len(tags)} candidate tags from {plc['tag_file']}")
        except (OSError, ValueError, KeyError) as exc:
            report("tag_list", False, str(exc), "Fix tag_file / include_patterns in settings.json")
    else:
        report("tag_list", True, f"{len(plc['modbus']['points'])} configured Modbus points")

    port = 44818 if plc["protocol"] == "ethernetip" else plc["modbus"].get("port", 502)
    ok, detail = tcp_check(plc["host"], port)
    report("plc_tcp", ok, f"{plc['host']}:{port} {detail}",
           "Check edge Ethernet IP, route, VLAN, cabling and PLC port; try nc -vz")
    dbfile = Path(cfg["local_db"])
    if dbfile.exists():
        try:
            with closing(sqlite3.connect(f"file:{dbfile}?mode=ro", uri=True, timeout=5)) as db:
                integrity = db.execute("PRAGMA quick_check").fetchone()[0]
            report("sqlite_integrity", integrity == "ok", f"{dbfile}: {integrity}",
                   "Back up SQLite, then investigate disk corruption without deleting the queue")
            counts = status(str(dbfile), recent=0)
            report("sqlite_queue", True, f"saved={counts['saved']} pending={counts['queued']} "
                   f"synced={counts['synced']} oldest_pending={counts['oldest_pending_id']}")
        except (OSError, sqlite3.Error) as exc:
            report("sqlite_integrity", False, f"{type(exc).__name__}: {exc}",
                   "Check sqlite file owner, permissions, storage and schema")
    else:
        report("sqlite_storage", True, f"Not created yet: {dbfile} (run collect-once)")
    disk = shutil.disk_usage(str(dbfile.parent if dbfile.parent.exists() else Path.cwd()))
    free_mb = disk.free / 1048576
    report("disk_space", free_mb > 100, f"{free_mb:.1f} MiB free",
           "Free up disk space safely; never delete unsynced SQLite data")

    host = os.environ.get("MARIADB_HOST", "65.20.79.196")
    port_str = os.environ.get("MARIADB_PORT", "3306")
    try:
        remote_port = int(port_str)
        ok, detail = tcp_check(host, remote_port)
        report("mariadb_tcp", ok, f"{host}:{remote_port} {detail}",
               "Check Internet/VPN, MariaDB firewall, listen address and port")
    except ValueError:
        report("mariadb_tcp", False, f"Invalid MARIADB_PORT: {port_str!r}", "Set a numeric port in .env")

    present = bool(os.environ.get("MARIADB_PASSWORD", "")) and not os.environ.get("MARIADB_PASSWORD", "").startswith("REPLACE_WITH_")
    report("mariadb_credentials", present, "MARIADB_PASSWORD configured" if present else "Not configured",
           "Copy .env.example to .env and set service account credentials")
    if os.environ.get("MARIADB_TLS", "off").lower() == "off":
        LOG.warning("DIAG_SECURITY MariaDB TLS is OFF; use a VPN/SSH tunnel or verified TLS on public networks")

    if include_remote_auth and present:
        try:
            from .synchronizer import remote_status
            result = remote_status(cfg)
            report("mariadb_authenticated", True,
                   f"remote_rows={result['remote_rows']} max_edge_id={result['last_remote_edge_id']}")
        except Exception as exc:
            LOG.exception("DIAG_REMOTE_AUTH_FAILED")
            report("mariadb_authenticated", False, f"{type(exc).__name__}: {exc}",
                   "Check MariaDB grants, credentials, table, firewall and TLS settings")
    if include_plc_probe:
        try:
            from .plc_reader import LivePLC
            with LivePLC(cfg) as live:
                points, errors = live.read()
                report("plc_live_values", bool(points),
                       f"valid_points={len(points)} failed_reads={len(errors)} not_found={len(live.not_found)}",
                       "Verify CCW tags and access, then run probe --trace-tags")
        except Exception as exc:
            LOG.exception("DIAG_PLC_READ_FAILED")
            report("plc_live_values", False, f"{type(exc).__name__}: {exc}",
                   "Check actual PLC program and pycomm3 driver communication")

    passed = sum(item["ok"] for item in results)
    LOG.info("DIAG_COMPLETE passed=%d failed=%d", passed, len(results) - passed)
    return {"checks": results, "passed": passed, "failed": len(results) - passed,
            "mode": "read_only", "remote_auth_requested": include_remote_auth,
            "plc_probe_requested": include_plc_probe}
