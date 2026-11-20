"""Ordered, idempotent SQLite -> MariaDB upload with full operator diagnostics."""
import logging
import threading
import time
from datetime import datetime, timezone
from .config import mariadb_settings
from .error_hints import explain
from .locks import SingleInstance
from .storage import initialize, mark_synced, pending, status

LOG = logging.getLogger("micro850.sync")
TABLE = "micro850_plc_samples"

INSERT_SQL = """
  INSERT INTO micro850_plc_samples
    (device_id, edge_id, sampled_at_utc, point_count, points_json, payload_sha256)
  VALUES (%s, %s, %s, %s, %s, %s)
"""
SELECT_DIGEST_SQL = """
  SELECT payload_sha256 FROM micro850_plc_samples
  WHERE device_id = %s AND edge_id = %s
"""


class PayloadConflict(RuntimeError):
    """A remote ID exists but its digest differs. Never silently overwrite."""


def utc_datetime(timestamp):
    parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("Sample timestamp is missing timezone")
    return parsed.astimezone(timezone.utc).replace(tzinfo=None)


def is_duplicate(exc):
    return bool(getattr(exc, "args", ())) and exc.args[0] == 1062


def connect_mariadb(settings=None):
    try:
        import pymysql
    except ImportError as exc:
        raise RuntimeError("Install requirements.txt to use MariaDB") from exc
    options = settings or mariadb_settings()
    LOG.info("MARIADB_CONNECTING host=%s port=%s database=%s user=%s tls=%s",
             options["host"], options["port"], options["database"], options["user"],
             "verified" if options.get("ssl_verify_cert") else "off")
    started = time.monotonic()
    conn = pymysql.connect(**options)
    LOG.info("MARIADB_CONNECTED host=%s duration_ms=%d", options["host"],
             round((time.monotonic() - started) * 1000))
    return conn


def sync_batch(cfg, connection_factory=None):
    """Commit remote first, ACK locally second; safely replay uncertain commits.

    If an error happens on the remote commit or local acknowledgement, the
    original pending rows remain. Remote UNIQUE(device_id, edge_id) + digest
    verification makes repeat sends idempotent.
    """
    started = time.monotonic()
    rows = pending(cfg["local_db"], cfg["sync_batch_size"])
    if not rows:
        LOG.debug("SYNC_IDLE pending=0 elapsed_ms=%d", round((time.monotonic() - started) * 1000))
        return {"synced": 0, "duplicates": 0}
    LOG.info("SYNC_BATCH_START count=%d first_edge_id=%d last_edge_id=%d total_points=%d",
             len(rows), rows[0]["edge_id"], rows[-1]["edge_id"], sum(r["point_count"] for r in rows))
    factory = connection_factory or connect_mariadb
    remote = None
    duplicates = 0
    try:
        remote = factory()
        with remote.cursor() as cursor:
            for row in rows:
                args = (cfg["device_id"], row["edge_id"], utc_datetime(row["sampled_at_utc"]),
                        row["point_count"], row["points_json"], row["payload_sha256"])
                LOG.debug("MARIADB_INSERT_ATTEMPT edge_id=%d sampled_at=%s point_count=%d digest=%s",
                          row["edge_id"], row["sampled_at_utc"], row["point_count"],
                          row["payload_sha256"][:12])
                try:
                    cursor.execute(INSERT_SQL, args)
                    LOG.debug("MARIADB_INSERT_OK edge_id=%d", row["edge_id"])
                except Exception as exc:
                    if not is_duplicate(exc):
                        LOG.error("MARIADB_INSERT_FAILED edge_id=%d cause=%s: %s",
                                  row["edge_id"], type(exc).__name__, exc)
                        raise
                    # ACK may have been lost on a previously committed insert.
                    cursor.execute(SELECT_DIGEST_SQL, (cfg["device_id"], row["edge_id"]))
                    existing = cursor.fetchone()
                    if not existing or existing[0] != row["payload_sha256"]:
                        LOG.critical("SYNC_DATA_CONFLICT edge_id=%d device_id=%s; "
                                     "remote payload differs, no overwrite performed", row["edge_id"],
                                     cfg["device_id"])
                        raise PayloadConflict(
                            f"Remote duplicate mismatch for device={cfg['device_id']} edge_id={row['edge_id']}"
                        ) from exc
                    duplicates += 1
                    LOG.warning("SYNC_SAFE_REPLAY edge_id=%d digest_verified=YES; previously committed row reused",
                                row["edge_id"])
        LOG.info("MARIADB_COMMIT_BEGIN count=%d", len(rows))
        remote.commit()
        LOG.info("MARIADB_COMMIT_CONFIRMED count=%d last_edge_id=%d", len(rows), rows[-1]["edge_id"])
    except Exception as exc:
        code, action = explain(exc, "mariadb")
        LOG.error("ERROR_HINT component=mariadb code=%s next=%s", code, action)
        LOG.exception("SYNC_REMOTE_ERROR ids=%d..%d; batch NOT acknowledged locally; "
                      "safe to retry; remote commit outcome may be unknown",
                      rows[0]["edge_id"], rows[-1]["edge_id"])
        if remote is not None:
            try:
                remote.rollback()
                LOG.debug("MARIADB_ROLLBACK_DONE")
            except Exception:
                LOG.exception("MARIADB_ROLLBACK_FAILED; replay remains safe")
        raise
    finally:
        if remote is not None:
            try:
                remote.close()
                LOG.debug("MARIADB_CONNECTION_CLOSED")
            except Exception:
                LOG.exception("MARIADB_CLOSE_FAILED; local ACK not attempted")
                raise
    try:
        LOG.debug("SQLITE_ACK_START count=%d", len(rows))
        mark_synced(cfg["local_db"], rows)
    except Exception as exc:
        code, action = explain(exc, "sqlite")
        LOG.error("ERROR_HINT component=sqlite code=%s next=%s", code, action)
        LOG.exception("SQLITE_ACK_FAILED after confirmed remote commit; local items retained "
                      "and will be safely retried using remote UNIQUE key")
        raise
    LOG.info("SYNC_BATCH_COMPLETE confirmed=%d duplicate_replays=%d last_edge_id=%d elapsed_ms=%d",
             len(rows), duplicates, rows[-1]["edge_id"],
             round((time.monotonic() - started) * 1000))
    return {"synced": len(rows), "duplicates": duplicates}


def sync_forever(cfg, stop_event=None):
    stop = stop_event or threading.Event()
    initialize(cfg["local_db"])
    failures = 0
    idle_ticks = 0
    with SingleInstance(cfg["local_db"], "synchronizer"):
        LOG.info("SYNC_RUNNING interval_s=%s retry_s=%s batch_size=%s device=%s",
                 cfg["sync_interval_seconds"], cfg["sync_retry_seconds"],
                 cfg["sync_batch_size"], cfg["device_id"])
        while not stop.is_set():
            try:
                out = sync_batch(cfg)
                if failures:
                    LOG.info("MARIADB_RECOVERED after_failures=%d synced_now=%d", failures, out["synced"])
                    failures = 0
                interval = cfg["sync_interval_seconds"]
                if out["synced"] == cfg["sync_batch_size"]:
                    interval = 0.05
                if out["synced"]:
                    idle_ticks = 0
                    snapshot = status(cfg["local_db"], 0)
                    LOG.info("SYNC_QUEUE_STATUS pending=%d synced=%d next_pending=%s",
                             snapshot["queued"], snapshot["synced"], snapshot["oldest_pending_id"])
                else:
                    idle_ticks += 1
                    if idle_ticks == 1 or idle_ticks % max(1, round(60 / interval)) == 0:
                        snapshot = status(cfg["local_db"], 0)
                        LOG.info("SYNC_IDLE_HEARTBEAT pending=%d synced=%d", snapshot["queued"],
                                 snapshot["synced"])
            except PayloadConflict:
                # A wrong device_id/local database pairing cannot be solved by
                # retrying; stop rather than hammering the database indefinitely.
                LOG.critical("SYNC_HALT_DATA_CONFLICT; manual investigation required; "
                             "do NOT reset edge IDs or delete SQLite", exc_info=True)
                raise
            except Exception:
                failures += 1
                snapshot = status(cfg["local_db"], 0)
                LOG.exception("SYNC_RETRY consecutive_failures=%d pending=%d oldest_pending=%s "
                              "retry_in_seconds=%s; collector can continue offline",
                              failures, snapshot["queued"], snapshot["oldest_pending_id"],
                              cfg["sync_retry_seconds"])
                interval = cfg["sync_retry_seconds"]
            stop.wait(interval)
    LOG.info("SYNC_STOPPED %s", {k: v for k, v in status(cfg["local_db"], 0).items()
                                if k != "recent"})


def remote_status(cfg):
    LOG.info("REMOTE_STATUS_CHECK device=%s", cfg["device_id"])
    with connect_mariadb() as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT COUNT(*), COALESCE(MAX(edge_id),0) FROM micro850_plc_samples WHERE device_id=%s",
                           (cfg["device_id"],))
            stored, max_id = cursor.fetchone()
            cursor.execute("SELECT @@version")
            version = cursor.fetchone()[0]
        conn.rollback()
    # MariaDB aggregate functions may return decimal.Decimal, which json.dumps
    # cannot serialize. These two aggregate results are always integral counts/IDs.
    stored = int(stored)
    max_id = int(max_id)
    LOG.info("REMOTE_STATUS_RESULT rows=%d max_edge_id=%d", stored, max_id)
    return {"device_id": cfg["device_id"], "remote_rows": stored,
            "last_remote_edge_id": max_id, "mariadb_version": version}
