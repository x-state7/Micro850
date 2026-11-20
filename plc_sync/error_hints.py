"""Practical, credential-free diagnostics for typical commissioning failures."""
import sqlite3

MARIADB_CODES = {
    1044: ("DB_PERMISSION", "Grant INSERT/SELECT on the destination table to the ingest account."),
    1045: ("DB_LOGIN_DENIED", "Check MARIADB_USER/password, connecting source IP, and MariaDB account host restriction."),
    1049: ("DB_UNKNOWN_DATABASE", "Create the configured database (ptechview_machine) on the server."),
    1062: ("DB_DUPLICATE", "Check matching digest; do not reset edge IDs or overwrite rows."),
    1142: ("DB_TABLE_PERMISSION", "Grant INSERT and SELECT on micro850_plc_samples."),
    1146: ("DB_TABLE_MISSING", "Run sql/create_table.sql once as an administrator."),
    1153: ("DB_PACKET_TOO_LARGE", "Reduce number of PLC tags or increase server max_allowed_packet safely."),
    1205: ("DB_LOCK_TIMEOUT", "Check for transactions locking the destination table; uploader will retry."),
    1213: ("DB_DEADLOCK", "Check other writers and transactions; uploader will retry."),
    1406: ("DB_FIELD_TOO_LONG", "Verify MariaDB schema and oversized tag payloads."),
    2003: ("DB_TCP_UNREACHABLE", "Check remote MariaDB IP/port, route, VPN, firewall, and bind-address."),
    2006: ("DB_SERVER_GONE", "Check DB service restarts, firewall, network and read/write timeout."),
    2013: ("DB_CONNECTION_LOST", "Check Internet/VPN and DB health; uncertain commits are retried safely."),
}


def explain(exc, component="unknown"):
    """Return (stable error code, corrective step), without values/passwords."""
    name = type(exc).__name__
    details = str(exc).lower()
    args = getattr(exc, "args", ())
    db_errno = args[0] if args and isinstance(args[0], int) else None

    if name == "PayloadConflict":
        return "DB_DATA_CONFLICT", "Stop uploader and verify device_id, local SQLite identity, and remote digest. Never overwrite."
    if component == "mariadb":
        if db_errno in MARIADB_CODES:
            return MARIADB_CODES[db_errno]
        if "ssl" in details or "tls" in details or "certificate" in details:
            return "DB_TLS_ERROR", "Check CA, certificate hostname, TLS mode and remote server TLS configuration."
        if isinstance(exc, (ConnectionError, TimeoutError, OSError)):
            return "DB_NETWORK_ERROR", "Verify Internet/VPN, MariaDB port, server availability and .env."
        if isinstance(exc, ValueError):
            return "DB_CONFIG_ERROR", "Review .env: credentials, host, port, TLS mode and certificate."
    if component == "sqlite":
        if isinstance(exc, sqlite3.Error):
            if "locked" in details:
                return "SQLITE_LOCKED", "Stop duplicate collector/sync instances; check other processes using this database."
            if "readonly" in details or "permission" in details:
                return "SQLITE_PERMISSIONS", "Check SQLite file and parent folder owner/write permissions."
            if "full" in details or "disk" in details:
                return "SQLITE_DISK", "Free disk space without deleting unacknowledged samples."
            if "malformed" in details or "corrupt" in details:
                return "SQLITE_INTEGRITY", "Stop services, back up SQLite and run PRAGMA integrity_check."
            return "SQLITE_ERROR", "Inspect the SQLite traceback, schema and filesystem health."
        if isinstance(exc, OSError):
            return "SQLITE_FILESYSTEM", "Check permissions and free space on the local filesystem."
    if component == "plc":
        if isinstance(exc, (TimeoutError, ConnectionRefusedError)):
            return "PLC_TCP_ERROR", "Check 192.168.2.8:44818, cable, NIC subnet, port, firewall, and PLC power."
        if isinstance(exc, OSError):
            return "PLC_NETWORK_ERROR", "Check PLC routing, address, subnet, interface and port 44818."
        if "no excel variables" in details or "tag list" in details:
            return "PLC_TAG_MAPPING", "Check CCW tag names, external access and use a small include_patterns set."
        if "cip" in details or "tag" in details:
            return "PLC_CIP_READ_ERROR", "Run --debug --trace-tags probe and verify firmware, CCW tags and CIP service."
    return "UNCLASSIFIED", "Read the preceding full Python traceback and run doctor for network/storage checks."
