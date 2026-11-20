"""Read-only Rockwell Micro850 EtherNet/IP and legacy Modbus TCP adapters."""
import logging
import re
import time
from . import log_setup
from .config import load_tags
from .storage import normalize_points

LOG = logging.getLogger("micro850.reader")
BRACKETED = re.compile(r"\[[^]]+\]$")


def available_cip_tags(plc, requested):
    """Intersect Excel allowlist with live controller metadata. No data is fabricated."""
    available = getattr(plc, "tags", {})
    if not available:
        raise RuntimeError("PLC did not expose a CIP tag list; verify access/firmware")
    casing = {key.casefold(): key for key in available.keys()}
    found, not_found = [], []
    for item in requested:
        name = item["name"]
        base = BRACKETED.sub("", name)
        matched = casing.get(base.casefold())
        if matched is None:
            not_found.append(name)
        else:
            # pycomm3 accepts indexed array tags as 'name[index]'.
            suffix = name[len(base):]
            found.append({"name": name, "query": matched + suffix, "type": item.get("type", "")})
    return found, not_found


def read_cip_batch(plc, selected, batch_size=20):
    """Only successful scalar results become data points; zeros and False are valid."""
    values, failures = {}, []
    LOG.info("PLC_READ_START selected_tags=%d batch_size=%d", len(selected), batch_size)
    all_started = time.monotonic()
    for start in range(0, len(selected), batch_size):
        chunk = selected[start:start + batch_size]
        chunk_started = time.monotonic()
        LOG.debug("CIP_BATCH_START index=%d-%d count=%d tags=%s", start + 1,
                  start + len(chunk), len(chunk), [item["name"] for item in chunk])
        try:
            result = plc.read(*(item["query"] for item in chunk))
            replies = [result] if len(chunk) == 1 else result
            if not isinstance(replies, (tuple, list)) or len(replies) != len(chunk):
                raise RuntimeError("Unexpected CIP response count")
        except Exception as exc:
            # Some PLCs reject a multiple-service packet. Fall back to single-tag
            # requests only on protocol request/response errors, not network loss.
            if len(chunk) <= 1 or exc.__class__.__name__ not in {"RequestError", "ResponseError"}:
                LOG.exception("CIP_BATCH_FAILED start=%d count=%d; reconnect will be attempted",
                              start + 1, len(chunk))
                raise
            LOG.warning("CIP_MULTI_READ_REJECTED start=%d count=%d error=%s; falling back to single tags",
                        start + 1, len(chunk), exc)
            replies = []
            for item in chunk:
                try:
                    replies.append(plc.read(item["query"]))
                except Exception as err:
                    if err.__class__.__name__ not in {"RequestError", "ResponseError"}:
                        raise
                    LOG.warning("CIP_SINGLE_READ_REJECTED tag=%s error=%s", item["name"], err)
                    replies.append(None)
        before_ok, before_fail = len(values), len(failures)
        for tag, reply in zip(chunk, replies):
            if reply is None or getattr(reply, "error", None) is not None:
                reason = str(getattr(reply, "error", "empty response"))
                failures.append((tag["name"], reason))
                if log_setup.tracing_tags():
                    LOG.warning("CIP_TAG_FAILED tag=%s error=%s", tag["name"], reason)
                continue
            val = getattr(reply, "value", None)
            if val is None:
                failures.append((tag["name"], "empty value"))
                if log_setup.tracing_tags():
                    LOG.warning("CIP_TAG_FAILED tag=%s error=empty_value", tag["name"])
                continue
            expected = tag.get("type", "").upper()
            if expected == "BOOL" and not isinstance(val, bool):
                reason = f"expected BOOL, received {type(val).__name__}"
                failures.append((tag["name"], reason))
                if log_setup.tracing_tags():
                    LOG.warning("CIP_TAG_FAILED tag=%s error=%s", tag["name"], reason)
                continue
            if expected in {"REAL", "LREAL"} and (isinstance(val, bool) or not isinstance(val, (int, float))):
                reason = f"expected REAL, received {type(val).__name__}"
                failures.append((tag["name"], reason))
                if log_setup.tracing_tags():
                    LOG.warning("CIP_TAG_FAILED tag=%s error=%s", tag["name"], reason)
                continue
            cleaned = normalize_points({tag["name"]: val})
            if cleaned:
                values.update(cleaned)
                if log_setup.tracing_tags():
                    LOG.info("CIP_TAG_OK tag=%s type=%s%s", tag["name"], type(val).__name__,
                             f" value={val!r}" if log_setup.showing_values() else "")
            else:
                failures.append((tag["name"], "unsupported or nonfinite value"))
                if log_setup.tracing_tags():
                    LOG.warning("CIP_TAG_FAILED tag=%s error=unsupported_or_nonfinite", tag["name"])
        LOG.debug("CIP_BATCH_END index=%d count=%d succeeded=%d failed=%d elapsed_ms=%d",
                  start + 1, len(chunk), len(values) - before_ok, len(failures) - before_fail,
                  round((time.monotonic() - chunk_started) * 1000))
    LOG.info("PLC_READ_END success=%d failed=%d duration_ms=%d", len(values), len(failures),
             round((time.monotonic() - all_started) * 1000))
    return values, failures


class LivePLC:
    """The connection remains open across polls; driver never writes to the PLC."""
    def __init__(self, cfg):
        self.cfg = cfg
        self.connection = None
        self.selected = None
        self.not_found = []

    def __enter__(self):
        plc_cfg = self.cfg["plc"]
        if plc_cfg["protocol"] == "ethernetip":
            try:
                from pycomm3 import LogixDriver
            except ImportError as exc:
                raise RuntimeError("Install requirements.txt to enable EtherNet/IP") from exc
            LOG.info("CIP_CONNECT_START host=%s port=44818 tag_metadata=enabled",
                     plc_cfg["host"])
            started = time.monotonic()
            driver = LogixDriver(plc_cfg["host"], init_program_tags=False)
            try:
                if not driver.open():
                    raise ConnectionError(f"Cannot open CIP connection to {plc_cfg['host']}:44818")
                requested = load_tags(plc_cfg["tag_file"],
                                      plc_cfg.get("include_patterns", ["*"]),
                                      plc_cfg.get("exclude_patterns", []))
                selected, missing = available_cip_tags(driver, requested)
                if not selected:
                    raise RuntimeError("No Excel variables match available live PLC tags")
                self.selected, self.not_found = selected, missing
                self.connection = driver
                LOG.info("CIP_CONNECTED host=%s selected=%d not_found=%d available_controller_tags=%d "
                         "connect_ms=%d", plc_cfg["host"], len(selected), len(missing),
                         len(getattr(driver, "tags", {})), round((time.monotonic() - started) * 1000))
                LOG.debug("CIP_SELECTED_TAGS %s", [t["name"] for t in selected])
                if missing:
                    LOG.warning("CIP_TAG_NOT_FOUND missing=%d examples=%s; inspect --debug log for full list",
                                len(missing), ", ".join(missing[:10]))
                    LOG.debug("CIP_ALL_NOT_FOUND %s", missing)
            except Exception:
                LOG.exception("CIP_CONNECT_OR_DISCOVERY_FAILED host=%s", plc_cfg["host"])
                driver.close()
                raise
        else:
            from .modbus_points import validate_plc
            validate_plc(dict(plc_cfg["modbus"], host=plc_cfg["host"]))
            self.selected = plc_cfg["modbus"]["points"]
            LOG.info("Modbus TCP selected; map supplied in settings.json")
        return self

    def __exit__(self, *exc):
        if self.connection is not None:
            LOG.info("CIP_DISCONNECT host=%s", self.cfg["plc"]["host"])
            self.connection.close()
            self.connection = None

    def read(self):
        plc_cfg = self.cfg["plc"]
        if plc_cfg["protocol"] == "ethernetip":
            if not self.connection:
                raise RuntimeError("CIP connection not open")
            return read_cip_batch(self.connection, self.selected, plc_cfg.get("batch_size", 20))
        from .modbus_points import read_points
        conf = dict(plc_cfg["modbus"], host=plc_cfg["host"])
        return read_points(conf), []
