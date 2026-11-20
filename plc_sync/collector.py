"""Durable local PLC poller, with per-cycle verification and reconnect diagnostics."""
import json
import logging
import threading
import time
from . import log_setup
from .error_hints import explain
from .locks import SingleInstance
from .plc_reader import LivePLC
from .storage import append_sample, initialize, status

LOG = logging.getLogger("micro850.collector")


def collect_once(cfg, reader=None):
    """Reader injection supports safe tests without touching hardware."""
    initialize(cfg["local_db"])
    start = time.monotonic()
    LOG.info("POLL_START device=%s plc=%s", cfg["device_id"], cfg.get("plc", {}).get("host", "test"))
    if reader is None:
        with LivePLC(cfg) as live:
            values, errors = live.read()
    else:
        values, errors = reader()
    sample_id = store_read(cfg, values, errors)
    LOG.info("POLL_END edge_id=%s valid=%d failed=%d elapsed_ms=%d",
             sample_id, len(values), len(errors), round((time.monotonic() - start) * 1000))
    return sample_id


def store_read(cfg, values, errors):
    started = time.monotonic()
    try:
        sample_id = append_sample(cfg["local_db"], values)
    except Exception as exc:
        code, action = explain(exc, "sqlite")
        LOG.error("ERROR_HINT component=sqlite code=%s next=%s", code, action)
        LOG.exception("SQLITE_WRITE_FAILED db=%s; sample not acknowledged", cfg["local_db"])
        raise
    if sample_id is None:
        LOG.warning("SQLITE_NO_SAMPLE valid_points=0; nothing fabricated; no local row created")
    else:
        LOG.info("SQLITE_SAVED edge_id=%s valid_points=%d write_ms=%d %s",
                 sample_id, len(values), round((time.monotonic() - started) * 1000),
                 log_setup.safe_preview(values))
    if errors:
        LOG.warning("PLC_TAG_ERRORS total=%d first_10=%s; --trace-tags gives every tag",
                    len(errors), errors[:10])
        if LOG.isEnabledFor(logging.DEBUG):
            for name, message in errors:
                LOG.debug("PLC_TAG_ERROR tag=%s cause=%s", name, message)
    if log_setup.showing_values():
        for name, value in values.items():
            LOG.info("PLC_VALUE tag=%s value=%r", name, value)
    return sample_id


def collect_forever(cfg, stop_event=None):
    stop = stop_event or threading.Event()
    initialize(cfg["local_db"])
    consecutive_failures = 0
    poll_number = 0
    with SingleInstance(cfg["local_db"], "collector"):
        LOG.info("COLLECTOR_RUNNING sample_interval_s=%s retry_s=%s",
                 cfg["sample_interval_seconds"], cfg["collect_retry_seconds"])
        while not stop.is_set():
            try:
                LOG.info("PLC_CONNECTING host=%s protocol=%s", cfg["plc"]["host"], cfg["plc"]["protocol"])
                with LivePLC(cfg) as live:
                    if consecutive_failures:
                        LOG.info("PLC_RECOVERED after_failed_connect_cycles=%d", consecutive_failures)
                    consecutive_failures = 0
                    while not stop.is_set():
                        poll_number += 1
                        started = time.monotonic()
                        LOG.info("POLL_START sequence=%d target=%s", poll_number, cfg["plc"]["host"])
                        values, errors = live.read()
                        sample_id = store_read(cfg, values, errors)
                        LOG.info("POLL_END sequence=%d edge_id=%s valid=%d failed=%d "
                                 "elapsed_ms=%d", poll_number, sample_id, len(values), len(errors),
                                 round((time.monotonic() - started) * 1000))
                        if poll_number % 10 == 0:
                            current = status(cfg["local_db"], 0)
                            LOG.info("COLLECTOR_HEARTBEAT polls=%d saved=%d pending=%d synced=%d",
                                     poll_number, current["saved"], current["queued"], current["synced"])
                        stop.wait(cfg["sample_interval_seconds"])
            except Exception as exc:
                consecutive_failures += 1
                component = "sqlite" if exc.__class__.__module__.startswith("sqlite3") else "plc"
                code, action = explain(exc, component)
                LOG.error("ERROR_HINT component=%s code=%s next=%s", component, code, action)
                LOG.exception("PLC_OR_SQLITE_FAILURE consecutive=%d; unsynced SQLite data retained; "
                              "retry_in_s=%s", consecutive_failures, cfg["collect_retry_seconds"])
                stop.wait(cfg["collect_retry_seconds"])
    LOG.info("COLLECTOR_STOPPED %s", json.dumps({k: v for k, v in status(cfg["local_db"], 0).items()
                                                  if k != "recent"}))
