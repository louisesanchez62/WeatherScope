#!/usr/bin/env python3
"""Build the monthly read model (obs_months) from the observations.

Run once after the initial load. Afterwards F5 keeps it up to date by
rebuilding only the months an import touched.

Usage:
    python3 scripts/build_read_model.py [--uri ...] [--db ...]
"""
import argparse
import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from weatherscope import bucket  # noqa: E402
from weatherscope.db import connect  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--uri")
    ap.add_argument("--db")
    args = ap.parse_args()
    t = time.perf_counter()
    bucket.build(connect(args.uri, args.db))
    print(f"built in {time.perf_counter() - t:.0f} s")


if __name__ == "__main__":
    main()
