#!/usr/bin/env python3
"""Load GHCN-Daily CSV files into MongoDB as one import batch.

Thin wrapper around weatherscope.f5.import_batch, which holds the import
policy (insert / unchanged / revised with history / rejected). The same code
is reached through `python3 -m weatherscope f5`.

Usage:
    python3 scripts/load_observations.py --label initial
    python3 scripts/load_observations.py --label f5-2021-2025 --start 2021-01-01 --end 2025-12-31
    python3 scripts/load_observations.py --label f5-extract --files path/to/2021.csv.gz --end 2021-12-31
"""
import argparse
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from weatherscope.db import connect  # noqa: E402
from weatherscope.f5 import import_batch  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--uri")
    ap.add_argument("--db")
    ap.add_argument("--label", required=True, help="batch label, e.g. initial or f5-2021-2025")
    ap.add_argument("--files", nargs="+", help="GHCN CSV files (default: downloaded by_station files)")
    ap.add_argument("--stations", nargs="+", metavar="ID", help="only these stations")
    ap.add_argument("--long-only", action="store_true", help="only stations flagged long_record")
    ap.add_argument("--include-unselected", action="store_true",
                    help="also accept stations of our areas left out of the initial load (e.g. above 800 m)")
    ap.add_argument("--start", default="1961-01-01")
    ap.add_argument("--end", default="2020-12-31")
    args = ap.parse_args()
    try:
        summary = import_batch(connect(args.uri, args.db), args.label, args.files, args.stations, args.long_only,
                               args.start, args.end, args.include_unselected)
    except ValueError as e:
        raise SystemExit(f"error: {e}")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
