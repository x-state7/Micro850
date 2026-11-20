"""End-to-end offline/ACK-loss test with in-memory fake MariaDB (no PLC/network)."""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from plc_sync.collector import collect_once
from plc_sync.config import load_tags
from plc_sync.plc_reader import available_cip_tags, read_cip_batch
from plc_sync.storage import append_sample, initialize, mark_synced, pending, status
from plc_sync.synchronizer import PayloadConflict, sync_batch


class FakeTag:
    def __init__(self, value=None, error=None):
        self.value, self.error = value, error


class FakePLC:
    tags = {"Data1": {}, "P000": {}, "DATALOG": {}}

    def read(self, *tags):
        replies = []
        for t in tags:
            if t == "Data1":
                replies.append(FakeTag(0.0))
            elif t == "P000":
                replies.append(FakeTag(False))
            elif t == "DATALOG[1]":
                replies.append(FakeTag(12.75))
            else:
                replies.append(FakeTag(error="Tag not found"))
        return replies[0] if len(replies) == 1 else replies


class DuplicateError(Exception):
    def __init__(self):
        super().__init__(1062, "Duplicate entry")


class FakeCursor:
    def __init__(self, conn):
        self.conn = conn
        self.latest_result = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, sql, params):
        if "INSERT INTO micro850_plc_samples" in sql:
            device, edge_id, sample_time, count, json_text, digest = params
            key = (device, edge_id)
            if key in self.conn.state or key in self.conn.staged:
                raise DuplicateError()
            self.conn.staged[key] = dict(digest=digest, count=count, data=json_text,
                                         sample_time=sample_time)
        elif "SELECT payload_sha256" in sql:
            key = (params[0], params[1])
            match = self.conn.staged.get(key) or self.conn.state.get(key)
            self.latest_result = (match["digest"],) if match else None
        else:
            raise AssertionError(sql)

    def fetchone(self):
        return self.latest_result


class FakeConnection:
    def __init__(self, state, ack_lost=False):
        self.state = state
        self.staged = {}
        self.ack_lost = ack_lost
        self.closed = False

    def cursor(self):
        return FakeCursor(self)

    def commit(self):
        self.state.update(self.staged)
        self.staged.clear()
        if self.ack_lost:
            # Remote commit succeeded but local Python never received confirmation.
            raise ConnectionError("Simulated lost commit acknowledgment")

    def rollback(self):
        self.staged.clear()

    def close(self):
        self.closed = True


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = str(Path(self.temp.name) / "edge.sqlite3")
        initialize(self.db)
        self.cfg = {"local_db": self.db, "device_id": "micro850-192-168-2-8", "sync_batch_size": 3}

    def test_tag_intersection_and_only_successful_points(self):
        selected, missing = available_cip_tags(FakePLC(), [
            {"name": "Data1", "type": "REAL"},
            {"name": "P000", "type": "BOOL"},
            {"name": "DATALOG[1]", "type": "REAL"},
            {"name": "Data404", "type": "REAL"},
        ])
        self.assertEqual(missing, ["Data404"])
        values, errors = read_cip_batch(FakePLC(), selected, batch_size=2)
        self.assertEqual(errors, [])
        self.assertEqual(values, {"Data1": 0.0, "P000": False, "DATALOG[1]": 12.75})
        self.assertEqual(collect_once(self.cfg, reader=lambda: (values, [])), 1)
        saved = pending(self.db, 20)
        self.assertEqual(json.loads(saved[0]["points_json"]), values)

    def test_bad_and_empty_values_not_inserted(self):
        self.assertIsNone(append_sample(self.db, {"bad": float("nan"), "missing": None}))
        self.assertEqual(status(self.db, 0)["saved"], 0)
        self.assertEqual(append_sample(self.db, {"false": False, "zero": 0, "bad": float("inf")}), 1)
        self.assertEqual(json.loads(pending(self.db, 1)[0]["points_json"]), {"false": False, "zero": 0})

    def test_offline_backlog_retained_and_ordered(self):
        for number in (1, 2, 3, 4):
            append_sample(self.db, {"a": number})
        remote = {}
        self.assertEqual(status(self.db, 0)["queued"], 4)
        self.assertEqual(sync_batch(self.cfg, connection_factory=lambda: FakeConnection(remote)),
                         {"synced": 3, "duplicates": 0})
        self.assertEqual(status(self.db, 0)["queued"], 1)
        self.assertEqual(sync_batch(self.cfg, connection_factory=lambda: FakeConnection(remote)),
                         {"synced": 1, "duplicates": 0})
        self.assertEqual(status(self.db, 0)["queued"], 0)
        self.assertEqual([key[1] for key in remote.keys()], [1, 2, 3, 4])
        self.assertEqual([json.loads(row["data"])["a"] for row in remote.values()], [1, 2, 3, 4])

    def test_commit_ack_lost_then_safe_replay(self):
        append_sample(self.db, {"pressure": 4.8})
        remote = {}
        with self.assertRaises(ConnectionError):
            sync_batch(self.cfg, connection_factory=lambda: FakeConnection(remote, ack_lost=True))
        self.assertEqual(status(self.db, 0)["queued"], 1)
        self.assertEqual(len(remote), 1)
        self.assertEqual(sync_batch(self.cfg, connection_factory=lambda: FakeConnection(remote)),
                         {"synced": 1, "duplicates": 1})
        self.assertEqual(len(remote), 1)
        self.assertEqual(status(self.db, 0)["queued"], 0)

    def test_duplicate_with_different_content_is_hard_error(self):
        append_sample(self.db, {"temperature": 10.5})
        remote = {(self.cfg["device_id"], 1): {"digest": "wrong digest", "data": "{}", "count": 0}}
        with self.assertRaises(PayloadConflict):
            sync_batch(self.cfg, connection_factory=lambda: FakeConnection(remote))
        self.assertEqual(status(self.db, 0)["queued"], 1)

    def test_local_ack_failure_retry_is_idempotent(self):
        append_sample(self.db, {"motor": True})
        remote = {}
        with patch("plc_sync.synchronizer.mark_synced", side_effect=sqlite3.OperationalError("disk failed")):
            with self.assertRaises(sqlite3.OperationalError):
                sync_batch(self.cfg, connection_factory=lambda: FakeConnection(remote))
        self.assertEqual(len(remote), 1)
        self.assertEqual(status(self.db, 0)["queued"], 1)
        self.assertEqual(sync_batch(self.cfg, connection_factory=lambda: FakeConnection(remote)),
                         {"synced": 1, "duplicates": 1})
        self.assertEqual(status(self.db, 0)["synced"], 1)

    def test_mark_synced_detects_wrong_digest(self):
        append_sample(self.db, {"x": 1})
        row = pending(self.db, 1)[0]
        row["payload_sha256"] = "incorrect"
        with self.assertRaises(RuntimeError):
            mark_synced(self.db, [row])
        self.assertEqual(status(self.db, 0)["queued"], 1)

    def test_tags_file_parses_all_excel_points(self):
        tag_path = Path(__file__).resolve().parents[1] / "config" / "tags.json"
        tags = load_tags(str(tag_path))
        self.assertEqual(len(tags), 528)
        self.assertIn({"name": "DATALOG[20]", "type": "REAL"}, tags)
        self.assertIn({"name": "P250", "type": "BOOL"}, tags)
        self.assertEqual(len(load_tags(str(tag_path), ["DATALOG*"], [])), 20)


if __name__ == "__main__":
    unittest.main()
