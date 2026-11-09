#!/usr/bin/env python3
"""Losing an instance: what the user receives when a shard is down.

Run after stopping a shard (python3 cluster.py kill shard2). The script picks
a station stored on that shard, then runs, through the normal entry point:
  1. F1 on that station;
  2. F3 on both areas;
  3. F3 on each area alone (on the zoned profile, the area whose shard is
     still up is answered completely).
Each answer is either complete or refused as UNAVAILABLE with what is
missing; nothing partial is presented as complete.

Usage:
    python3 -m scripts.distributed.failure_demo hashed|zoned shard2RS [--db NAME]
"""
import argparse
import json

from weatherscope.__main__ import execute
from weatherscope.deploy import profile_db, shard_status, station_shards


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("profile", choices=("hashed", "zoned"))
    ap.add_argument("shard", help="name of the stopped shard, e.g. shard2RS")
    ap.add_argument("--db")
    a = ap.parse_args()
    db = profile_db(a.profile, a.db)
    if shard_status(db).get(a.shard, {}).get("up", True):
        raise SystemExit(f"{a.shard} is still up: stop it first with python3 cluster.py kill "
                         f"{a.shard.replace('RS', '')}")
    try:
        owned = sorted(s for s, sh in station_shards(db).items() if sh == a.shard)
    except Exception as e:  # metadata unreachable (primary shard down): show what the services answer
        owned = []
        print(f"(cannot map stations to shards: {e.__class__.__name__})")
    station = owned[0] if owned else "USW00094847"
    extra = ["--profile", a.profile, "--json"] + (["--db", a.db] if a.db else [])
    requests = {
        f"F1 on {station} (stored on {a.shard})": ["f1", station, "--from", "2014-01-01", "--to", "2014-12-31"],
        "F3 both areas": ["f3", "--element", "SNWD", "--months", "12", "1", "2"],
        "F3 Michigan only": ["f3", "--element", "SNWD", "--months", "12", "1", "2", "--area", "michigan"],
        "F3 southern Germany only": ["f3", "--element", "SNWD", "--months", "12", "1", "2", "--area",
                                     "southern_germany"],
    }
    for label, argv in requests.items():
        r = execute(argv + extra)
        summary = {k: r.get(k) for k in ("status", "complete", "missing", "message") if k in r}
        if r.get("complete"):
            summary["status"] = "OK"
            summary["rows"] = len(r.get("periods", r.get("all_months", [])))
        print(f"{label}:\n{json.dumps(summary, indent=2, default=str)}\n")


if __name__ == "__main__":
    main()
