"""Diagnostic regression tests. No real PLC, MariaDB or Internet access needed."""
import io
import logging
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from plc_sync import log_setup
from plc_sync.collector import collect_once
from plc_sync.diagnostics import run_doctor
from plc_sync.error_hints import explain
from plc_sync.log_setup import SafeFormatter, configure_logging
from plc_sync.plc_reader import read_cip_batch
from plc_sync.storage import initialize, status
from plc_sync.synchronizer import sync_batch
from tests.test_pipeline import FakePLC, FakeConnection


class DiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.db = str(self.root / "data" / "edge.sqlite3")
        self.cfg = {"local_db": self.db, "device_id": "test-device", "sync_batch_size": 3,
                    "plc": {"protocol": "ethernetip", "host": "192.168.2.8",
                            "tag_file": str(self.root / "tags.json"), "batch_size": 2,
                            "include_patterns": ["*"], "exclude_patterns": []}}
        (self.root / "tags.json").write_text('[{"name":"Data1","type":"REAL"}]')
        initialize(self.db)
        self.addCleanup(self.reset_trace)

    @staticmethod
    def reset_trace():
        log_setup.TRACE_TAGS = False
        log_setup.SHOW_VALUES = False

    def test_secret_redacted_in_regular_messages_and_tracebacks(self):
        formatter = SafeFormatter("%(message)s", ["examplePASSWORD321"])
        try:
            raise ValueError("pw=examplePASSWORD321")
        except ValueError:
            import sys
            record = logging.LogRecord("test", logging.ERROR, __file__, 1,
                                       "Password: examplePASSWORD321", (), sys.exc_info())
        output = formatter.format(record)
        self.assertNotIn("examplePASSWORD321", output)
        self.assertIn("[REDACTED]", output)

    def test_rolling_log_created_and_value_logging_off_by_default(self):
        with patch.dict(os.environ, {"MARIADB_PASSWORD": "my-secret-678"}):
            location = configure_logging("test-diagnostics", self.root)
            logger = logging.getLogger("micro850.test")
            logger.info("TAG=temperature pw=my-secret-678")
            for handler in logging.getLogger().handlers:
                handler.flush()
            logged = location.read_text()
            self.assertIn("LOGGING_READY", logged)
            self.assertNotIn("my-secret-678", logged)
            self.assertIn("[REDACTED]", logged)
            self.assertFalse(log_setup.showing_values())

    def test_trace_tag_and_value_opt_in(self):
        selected = [{"name": "Data1", "query": "Data1", "type": "REAL"}]
        configure_logging("trace-test", self.root, trace_tags=True, show_values=True)
        with self.assertLogs("micro850.reader", level="INFO") as received:
            values, errors = read_cip_batch(FakePLC(), selected, 1)
        self.assertEqual(values, {"Data1": 0.0})
        self.assertEqual(errors, [])
        self.assertTrue(any("CIP_TAG_OK tag=Data1" in line and "value=0.0" in line
                            for line in received.output))

    def test_collector_warns_and_does_not_fabricate_samples(self):
        with self.assertLogs("micro850.collector", level="WARNING") as logs:
            result = collect_once(self.cfg, reader=lambda: ({}, [("A", "not_found")]))
        self.assertIsNone(result)
        self.assertEqual(status(self.db, 0)["queued"], 0)
        self.assertTrue(any("SQLITE_NO_SAMPLE" in line for line in logs.output))
        self.assertTrue(any("PLC_TAG_ERRORS" in line for line in logs.output))

    def test_sync_reports_confirmed_batch_and_safe_replay(self):
        collect_once(self.cfg, reader=lambda: ({"Data1": 2.5}, []))
        state = {}
        with self.assertLogs("micro850.sync", level="INFO") as messages:
            result = sync_batch(self.cfg, connection_factory=lambda: FakeConnection(state))
        self.assertEqual(result["synced"], 1)
        self.assertTrue(any("MARIADB_COMMIT_CONFIRMED" in line for line in messages.output))
        self.assertTrue(any("SYNC_BATCH_COMPLETE" in line for line in messages.output))
        self.assertEqual(status(self.db, 0)["queued"], 0)

    def test_error_hints_for_common_mariadb_and_sqlite_failures(self):
        self.assertEqual(explain(Exception(1045, "access denied"), "mariadb")[0], "DB_LOGIN_DENIED")
        self.assertEqual(explain(Exception(1146, "missing table"), "mariadb")[0], "DB_TABLE_MISSING")
        self.assertEqual(explain(Exception(2003, "can't connect"), "mariadb")[0], "DB_TCP_UNREACHABLE")
        import sqlite3
        self.assertEqual(explain(sqlite3.OperationalError("database is locked"), "sqlite")[0],
                         "SQLITE_LOCKED")
        self.assertEqual(explain(TimeoutError("timeout"), "plc")[0], "PLC_TCP_ERROR")

    def test_doctor_reports_checks_without_writing_or_using_credentials(self):
        with patch("plc_sync.diagnostics.tcp_check", return_value=(False, "offline")):
            with patch.dict(os.environ, {"MARIADB_PASSWORD": "", "MARIADB_TLS": "off"}):
                with self.assertLogs("micro850.doctor", level="INFO") as received:
                    output = run_doctor(self.cfg)
        self.assertGreater(output["failed"], 0)
        self.assertEqual(output["mode"], "read_only")
        self.assertTrue(any(item["check"] == "sqlite_integrity" and item["ok"]
                            for item in output["checks"]))
        self.assertTrue(any(item["check"] == "plc_tcp" and not item["ok"]
                            for item in output["checks"]))
        self.assertTrue(any("DIAG_COMPLETE" in line for line in received.output))
        self.assertEqual(status(self.db, 0)["saved"], 0)


if __name__ == "__main__":
    unittest.main()
