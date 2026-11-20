"""Online-safe SQLite backup using sqlite3.Connection.backup (compatible with WAL)."""
import argparse
import sqlite3
from pathlib import Path


def main():
    p = argparse.ArgumentParser()
    p.add_argument("source", type=Path)
    p.add_argument("destination", type=Path)
    args = p.parse_args()
    if not args.source.is_file():
        p.error(f"Source not found: {args.source}")
    if args.source.resolve() == args.destination.resolve():
        p.error("Destination must be a different path")
    args.destination.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(f"file:{args.source.resolve()}?mode=ro", uri=True) as src:
        with sqlite3.connect(str(args.destination)) as dest:
            src.backup(dest)
    print(f"Backup written: {args.destination}")


if __name__ == "__main__":
    main()
