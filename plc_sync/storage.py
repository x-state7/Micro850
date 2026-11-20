"""Local append-only SQLite samples and safe outbox acknowledgments."""
import hashlib
import json
import math
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


def now_utc():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


@contextmanager
def connect(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(str(path), timeout=15)
    db.execute("PRAGMA busy_timeout=15000")
    db.execute("PRAGMA synchronous=FULL")
    db.row_factory = sqlite3.Row
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def initialize(path):
    with connect(path) as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.executescript("""
        CREATE TABLE IF NOT EXISTS samples (
            edge_id INTEGER PRIMARY KEY AUTOINCREMENT,
            sampled_at_utc TEXT NOT NULL,
            point_count INTEGER NOT NULL,
            points_json TEXT NOT NULL,
            payload_sha256 TEXT NOT NULL,
            synced_at_utc TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_samples_pending
          ON samples(synced_at_utc, edge_id);
        """)


def normalize_points(points):
    cleaned = {}
    for key, val in points.items():
        if not isinstance(key, str) or not key:
            continue
        if isinstance(val, bool):
            cleaned[key] = val
        elif isinstance(val, int):
            cleaned[key] = val
        elif isinstance(val, float) and math.isfinite(val):
            cleaned[key] = val
        elif isinstance(val, str):
            cleaned[key] = val
        # None, NaN, Inf, structures, arrays, failed or unsupported reads: discarded.
    return cleaned


def make_digest(sampled_at_utc, points_json):
    return hashlib.sha256((sampled_at_utc + "\0" + points_json).encode("utf-8")).hexdigest()


def append_sample(path, points, sampled_at_utc=None):
    cleaned = normalize_points(points)
    if not cleaned:
        return None
    timestamp = sampled_at_utc or now_utc()
    payload = json.dumps(cleaned, separators=(",", ":"), ensure_ascii=False,
                         allow_nan=False, sort_keys=True)
    digest = make_digest(timestamp, payload)
    with connect(path) as db:
        cursor = db.execute("""
            INSERT INTO samples(sampled_at_utc, point_count, points_json, payload_sha256)
            VALUES (?, ?, ?, ?)
        """, (timestamp, len(cleaned), payload, digest))
        return cursor.lastrowid


def pending(path, limit):
    with connect(path) as db:
        records = db.execute("""
            SELECT edge_id, sampled_at_utc, point_count, points_json, payload_sha256
            FROM samples WHERE synced_at_utc IS NULL ORDER BY edge_id ASC LIMIT ?
        """, (limit,)).fetchall()
        return [dict(r) for r in records]


def mark_synced(path, rows):
    if not rows:
        return
    with connect(path) as db:
        db.execute("BEGIN IMMEDIATE")
        timestamp = now_utc()
        for row in rows:
            cur = db.execute("""
                UPDATE samples SET synced_at_utc = ?
                WHERE edge_id = ? AND payload_sha256 = ? AND synced_at_utc IS NULL
            """, (timestamp, row["edge_id"], row["payload_sha256"]))
            if cur.rowcount != 1:
                raise RuntimeError(f"Local sample {row['edge_id']} changed or already acknowledged")


def status(path, recent=2):
    with connect(path) as db:
        total, queued, synced, maximum = db.execute("""
            SELECT COUNT(*),
              COALESCE(SUM(CASE WHEN synced_at_utc IS NULL THEN 1 ELSE 0 END),0),
              COALESCE(SUM(CASE WHEN synced_at_utc IS NOT NULL THEN 1 ELSE 0 END),0),
              COALESCE(MAX(edge_id),0) FROM samples
        """).fetchone()
        oldest = db.execute("SELECT MIN(edge_id) FROM samples WHERE synced_at_utc IS NULL").fetchone()[0]
        latest = db.execute("""
            SELECT edge_id,sampled_at_utc,point_count,points_json,synced_at_utc
            FROM samples ORDER BY edge_id DESC LIMIT ?
        """, (recent,)).fetchall()
        recent_out = []
        for r in latest:
            row = dict(r)
            contents = json.loads(row.pop("points_json"))
            row["point_names"] = list(contents.keys())[:20]
            row["point_preview"] = {k: contents[k] for k in row["point_names"]}
            recent_out.append(row)
        return {"saved": total, "queued": queued, "synced": synced,
                "last_local_id": maximum, "oldest_pending_id": oldest,
                "recent": recent_out}
