"""Manual CLI: python -m plc_sync --config config/settings.json [flags] <command>."""
import argparse
import json
import logging
import os
import signal
import sys
import threading
from pathlib import Path
from .collector import collect_forever, collect_once
from .config import load_config, load_tags
from .diagnostics import run_doctor
from .log_setup import configure_logging
from .plc_reader import LivePLC
from .storage import initialize, status
from .synchronizer import PayloadConflict, remote_status, sync_batch, sync_forever

LOG = logging.getLogger("micro850.cli")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Micro850 PLC -> SQLite -> MariaDB (no Docker)")
    parser.add_argument("--config", default="config/settings.json")
    parser.add_argument("--debug", action="store_true", help="DEBUG-level terminal logs and Python diagnostics")
    parser.add_argument("--trace-tags", action="store_true", help="Log every attempted tag and tag read result")
    parser.add_argument("--show-values", action="store_true", help="Log actual PLC point values (sensitive)")
    parser.add_argument("--no-log-file", action="store_true", help="Disable rolling logs in logs/<command>.log")
    parser.add_argument("--doctor-live", action="store_true", help="For doctor: read-only PLC probe and remote login")
    parser.add_argument("command", choices=("doctor", "probe", "collect-once", "collect", "sync-once",
                                            "sync", "status", "remote-check", "tags"))
    args = parser.parse_args(argv)
    project_root = Path(args.config).expanduser().resolve().parent.parent
    try:
        # Pre-load secrets so even startup exceptions are redacted in log files.
        try:
            from dotenv import load_dotenv
            load_dotenv(project_root / ".env", override=False)
        except ImportError:
            pass
        configure_logging(args.command, project_root, debug=args.debug, trace_tags=args.trace_tags,
                          show_values=args.show_values, no_log_file=args.no_log_file)
        cfg = load_config(args.config)
        LOG.info("START command=%s device=%s protocol=%s plc_host=%s sqlite=%s",
                 args.command, cfg["device_id"], cfg["plc"]["protocol"],
                 cfg["plc"]["host"], cfg["local_db"])
        if args.show_values:
            LOG.warning("VALUE_LOGGING_ENABLED actual PLC values may appear on terminal and in log files")
        if args.command in {"collect-once", "collect", "sync-once", "sync", "status"}:
            initialize(cfg["local_db"])
        if args.command == "tags":
            plc = cfg["plc"]
            if plc["protocol"] == "ethernetip":
                tags = load_tags(plc["tag_file"], plc.get("include_patterns", ["*"]),
                                 plc.get("exclude_patterns", []))
                data = {"configured": len(tags), "sample_names": [t["name"] for t in tags[:30]]}
            else:
                data = {"configured": len(plc["modbus"]["points"])}
            LOG.info("TAG_CONFIG configured=%d", data["configured"])
            print(json.dumps(data, indent=2))
        elif args.command == "doctor":
            result = run_doctor(cfg, include_remote_auth=args.doctor_live,
                                include_plc_probe=args.doctor_live)
            print(json.dumps(result, indent=2))
            return 2 if result["failed"] else 0
        elif args.command == "status":
            result = status(cfg["local_db"], recent=2)
            LOG.info("QUEUE_STATUS saved=%d pending=%d synced=%d last_edge_id=%d oldest_pending=%s",
                     result["saved"], result["queued"], result["synced"], result["last_local_id"],
                     result["oldest_pending_id"])
            print(json.dumps(result, indent=2))
        elif args.command == "remote-check":
            result = remote_status(cfg)
            LOG.info("REMOTE_CHECK_SUCCESS rows=%d max_edge_id=%d", result["remote_rows"],
                     result["last_remote_edge_id"])
            print(json.dumps(result, indent=2))
        elif args.command == "probe":
            LOG.info("PROBE_READ_ONLY start; no data will be stored")
            with LivePLC(cfg) as live:
                points, errors = live.read()
                result = {"plc": cfg["plc"]["host"], "live_point_count": len(points),
                          "not_found_in_plc": len(live.not_found), "failed_reads": len(errors),
                          "failed_examples": errors[:15], "values": points}
                LOG.info("PROBE_COMPLETE valid=%d failed=%d missing=%d", len(points),
                         len(errors), len(live.not_found))
                print(json.dumps(result, indent=2))
        elif args.command == "collect-once":
            edge_id = collect_once(cfg)
            print(json.dumps({"saved_edge_id": edge_id, "local": status(cfg["local_db"], 0)}, indent=2))
        elif args.command == "sync-once":
            print(json.dumps({"result": sync_batch(cfg), "local": status(cfg["local_db"], 0)}, indent=2))
        else:
            stop = threading.Event()
            signal.signal(signal.SIGINT, lambda *_: stop.set())
            signal.signal(signal.SIGTERM, lambda *_: stop.set())
            if args.command == "collect":
                collect_forever(cfg, stop)
            else:
                sync_forever(cfg, stop)
        LOG.info("STOP command=%s completed", args.command)
        return 0
    except PayloadConflict:
        LOG.critical("FATAL_DATA_CONFLICT; stop uploader and inspect device/edge_id mapping. "
                     "Exit code 42 prevents automatic systemd restart.", exc_info=True)
        return 42
    except Exception:
        # Do not silently swallow startup, DB, permissions or PLC failures.
        LOG.exception("FATAL command=%s; see exception and traceback above", args.command)
        return 1


if __name__ == "__main__":
    sys.exit(main())
