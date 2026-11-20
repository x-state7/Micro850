"""Configuration loader; no passwords in JSON or source control."""
import fnmatch
import json
import os
import re
from pathlib import Path

DEVICE_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")


def load_config(path):
    path = Path(path).expanduser().resolve()
    with path.open(encoding="utf-8") as fh:
        cfg = json.load(fh)
    project_root = path.parent.parent
    try:
        from dotenv import load_dotenv
        load_dotenv(project_root / ".env", override=False)
    except ImportError:
        # Local collection/probing works without the optional env helper;
        # synchronizing still requires env variables.
        pass
    cfg["local_db"] = str((path.parent / cfg["local_db"]).resolve())
    plc = cfg["plc"]
    if "tag_file" in plc:
        plc["tag_file"] = str((path.parent / plc["tag_file"]).resolve())
    validate_config(cfg)
    return cfg


def validate_config(cfg):
    if not DEVICE_RE.fullmatch(str(cfg.get("device_id", ""))):
        raise ValueError("device_id must be 1..64 ASCII letters, numbers, '.', '_' or '-'")
    if not str(cfg.get("local_db", "")).strip():
        raise ValueError("local_db is required")
    plc = cfg.get("plc", {})
    if not isinstance(plc.get("host"), str) or not plc["host"].strip():
        raise ValueError("plc.host is required")
    if plc.get("protocol") not in {"ethernetip", "modbus_tcp"}:
        raise ValueError("plc.protocol must be ethernetip or modbus_tcp")
    if plc["protocol"] == "ethernetip":
        if not plc.get("tag_file"):
            raise ValueError("plc.tag_file must be set for EtherNet/IP")
        if type(plc.get("batch_size", 20)) is not int or not 1 <= plc.get("batch_size", 20) <= 100:
            raise ValueError("plc.batch_size must be 1..100")
        for key in ("include_patterns", "exclude_patterns"):
            items = plc.get(key, [])
            if not isinstance(items, list) or not all(isinstance(p, str) and p for p in items):
                raise ValueError(f"{key} must be a list of glob patterns")
    else:
        from .modbus_points import validate_plc
        modbus = dict(plc["modbus"], host=plc["host"])
        validate_plc(modbus)
    for key in ("sample_interval_seconds", "collect_retry_seconds",
                "sync_interval_seconds", "sync_retry_seconds"):
        value = cfg.get(key)
        if type(value) not in (int, float) or not 0.1 <= value <= 3600:
            raise ValueError(f"{key} must be 0.1..3600 seconds")
    limit = cfg.get("sync_batch_size")
    if type(limit) is not int or not 1 <= limit <= 1000:
        raise ValueError("sync_batch_size must be 1..1000")


def load_tags(tag_file, include_patterns=None, exclude_patterns=None):
    with open(tag_file, encoding="utf-8") as fh:
        raw = json.load(fh)
    rows = raw["tags"] if isinstance(raw, dict) else raw
    if not isinstance(rows, list):
        raise ValueError("tag file needs a tags array")
    include = include_patterns if include_patterns is not None else ["*"]
    exclude = exclude_patterns or []
    filtered, seen = [], set()
    for obj in rows:
        if not isinstance(obj, dict) or not isinstance(obj.get("name"), str):
            raise ValueError("Every tag needs a name")
        name = obj["name"].strip()
        if not name or len(name) > 120:
            raise ValueError(f"Invalid tag name: {name!r}")
        if name.lower() in seen:
            continue
        seen.add(name.lower())
        if (any(fnmatch.fnmatchcase(name, p) for p in include)
                and not any(fnmatch.fnmatchcase(name, p) for p in exclude)):
            filtered.append({"name": name, "type": obj.get("type", "")})
    if not filtered:
        raise ValueError("No tags remain after selection filters")
    return filtered


def mariadb_settings():
    password = os.environ.get("MARIADB_PASSWORD", "")
    if not password or password.startswith("REPLACE_WITH_"):
        raise ValueError("Set MARIADB_PASSWORD in .env (not the root password)")
    settings = {
        "host": os.environ.get("MARIADB_HOST", "65.20.79.196"),
        "port": int(os.environ.get("MARIADB_PORT", "3306")),
        "user": os.environ.get("MARIADB_USER", "plc_ingest"),
        "password": password,
        "database": os.environ.get("MARIADB_DATABASE", "ptechview_machine"),
        "charset": "utf8mb4", "autocommit": False,
        "connect_timeout": 5, "read_timeout": 15, "write_timeout": 15,
    }
    if not 1 <= settings["port"] <= 65535:
        raise ValueError("MARIADB_PORT out of range")
    tls = os.environ.get("MARIADB_TLS", "off").lower()
    if tls == "verify":
        ca = os.environ.get("MARIADB_SSL_CA", "")
        if not ca or not os.path.isfile(ca):
            raise ValueError("MARIADB_TLS=verify requires valid MARIADB_SSL_CA")
        settings.update(ssl_ca=ca, ssl_verify_cert=True, ssl_verify_identity=True)
    elif tls != "off":
        raise ValueError("MARIADB_TLS must be 'off' or 'verify'")
    return settings
