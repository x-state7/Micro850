"""Operator-friendly terminal + size-limited diagnostic file logging.

Passwords are redacted in both formatted messages and tracebacks. No PLC
values are logged unless the operator explicitly enables --show-values.
"""
import logging
import os
import re
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

TRACE_TAGS = False
SHOW_VALUES = False


class SecretFilter(logging.Filter):
    def __init__(self, secrets=()):
        super().__init__()
        self.secrets = [s for s in secrets if isinstance(s, str) and len(s) >= 3]

    def filter(self, record):
        # Resolve formatted messages before redaction, including exception text.
        # The exception formatter runs on this record later; a formatter that
        # scrubs the *whole* final string also protects exception stack traces.
        return True


class SafeFormatter(logging.Formatter):
    def __init__(self, fmt, secrets=()):
        super().__init__(fmt=fmt, datefmt="%Y-%m-%d %H:%M:%S")
        self.secrets = [s for s in secrets if isinstance(s, str) and len(s) >= 3]

    def format(self, record):
        message = super().format(record)
        for secret in self.secrets:
            message = message.replace(secret, "[REDACTED]")
        message = re.sub(r"(?i)(password|passwd|pwd|token|secret)\s*[=:]\s*([^\s,;]+)",
                         r"\1=[REDACTED]", message)
        return message


def configure_logging(command, project_root, *, debug=False, trace_tags=False,
                      show_values=False, no_log_file=False):
    global TRACE_TAGS, SHOW_VALUES
    TRACE_TAGS = trace_tags
    SHOW_VALUES = show_values
    requested = os.environ.get("LOG_LEVEL", "INFO").upper()
    level = logging.DEBUG if debug else getattr(logging, requested, logging.INFO)
    if not isinstance(level, int):
        level = logging.INFO
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    for handler in root.handlers[:]:
        root.removeHandler(handler)
        handler.close()
    secrets = [os.environ.get("MARIADB_PASSWORD", "")]
    fmt = "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(level)
    console.setFormatter(SafeFormatter(fmt, secrets))
    root.addHandler(console)
    log_path = None
    if not no_log_file:
        destination = os.environ.get("MICRO850_LOG_DIR", "logs")
        directory = Path(destination)
        if not directory.is_absolute():
            directory = Path(project_root) / directory
        try:
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            # Console and log files may contain process values when opted in.
            directory.chmod(0o700)
            log_path = directory / f"{command}.log"
            rotate = RotatingFileHandler(str(log_path), maxBytes=5 * 1024 * 1024,
                                         backupCount=3, encoding="utf-8")
            log_path.chmod(0o600)
            rotate.setLevel(logging.DEBUG)
            rotate.setFormatter(SafeFormatter(fmt, secrets))
            root.addHandler(rotate)
        except OSError as exc:
            logging.getLogger("micro850.logging").warning(
                "LOG_FILE_UNAVAILABLE path=%s error=%s; continuing with terminal logging",
                directory, exc)
    logging.getLogger("micro850.logging").info(
        "LOGGING_READY command=%s console_level=%s file=%s file_level=DEBUG "
        "trace_tags=%s show_values=%s pid=%d",
        command, logging.getLevelName(level), log_path or "disabled", trace_tags,
        show_values, os.getpid())
    return log_path


def tracing_tags():
    return TRACE_TAGS


def showing_values():
    return SHOW_VALUES


def safe_preview(points, limit=5):
    if not points:
        return "{}"
    if showing_values():
        import json
        return json.dumps(dict(list(points.items())[:limit]), ensure_ascii=False,
                          default=str, sort_keys=True)
    return "names=" + repr(list(points.keys())[:limit])
