"""Regression: MariaDB's Decimal aggregate results must print as JSON integers."""
import json
import unittest
from decimal import Decimal
from unittest.mock import patch
from plc_sync.synchronizer import remote_status


class FakeCursor:
    def __init__(self):
        self.query_count = 0

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, *args):
        self.query_count += 1

    def fetchone(self):
        if self.query_count == 1:
            return Decimal("0"), Decimal("0")
        return ("11.4.0-MariaDB",)


class FakeConnection:
    def cursor(self):
        return FakeCursor()

    def rollback(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


class RemoteCheckTests(unittest.TestCase):
    def test_decimal_aggregate_results_are_json_serializable_as_integers(self):
        cfg = {"device_id": "micro850-192-168-2-8"}
        with patch("plc_sync.synchronizer.connect_mariadb", return_value=FakeConnection()):
            result = remote_status(cfg)
        self.assertEqual(result["remote_rows"], 0)
        self.assertIs(type(result["remote_rows"]), int)
        self.assertIs(type(result["last_remote_edge_id"]), int)
        self.assertIs(type(result["mariadb_version"]), str)
        self.assertEqual(json.loads(json.dumps(result)), result)


if __name__ == "__main__":
    unittest.main()
