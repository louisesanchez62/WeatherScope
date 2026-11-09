#!/usr/bin/env python3
"""E2: local versus distributed operation, on the full archive.

Times the same requests (bench.E2_REQUESTS: narrow, long-range, multi-station)
on one deployment, through the normal entry point. Run it once per deployment,
with only that deployment running, so the three never compete for the same
CPU, disk and memory during a measurement:

    python3 -m scripts.experiments.e2 local      # scripts/local_server.sh running, cluster stopped
    python3 -m scripts.experiments.e2 hashed     # cluster running, local server stopped
    python3 -m scripts.experiments.e2 zoned

Each run starts with a warm-up per request (reported separately; the cache is
warm for the timed runs). For a cluster profile, also records the data balance
per shard. Answers carry a fingerprint, so equal answers across deployments can
be checked from the result files. Writes results/e2/e2_<profile>.json.
"""
import argparse
import datetime as dt
import json
import pathlib

from scripts.experiments.bench import E2_REQUESTS, machine, storage, time_requests
from weatherscope.deploy import is_cluster, profile_db

ROOT = pathlib.Path(__file__).resolve().parents[2]
OUT = ROOT / "results" / "e2"


def balance(db) -> dict:
    out = {}
    for c in ("observations", "obs_months"):
        rows = db[c].aggregate([{"$collStats": {"storageStats": {}}}])
        out[c] = {r["shard"]: {"documents": r["storageStats"]["count"],
                               "data_mb": round(r["storageStats"]["size"] / 2**20, 1)} for r in rows}
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("profile", choices=("local", "hashed", "zoned"))
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--heavy-runs", type=int, default=3)
    a = ap.parse_args()
    db = profile_db(a.profile)
    res = {"profile": a.profile, "generated_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
           "machine": machine(db), "storage": storage(db),
           "cache_condition": "one warm-up run per request, then timed runs on a warm cache; "
                              "only this deployment running"}
    if is_cluster(db):
        res["balance"] = balance(db)
        print(json.dumps(res["balance"], indent=2))
    res["requests"] = time_requests(E2_REQUESTS, ["--profile", a.profile], a.runs, a.heavy_runs)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"e2_{a.profile}.json").write_text(json.dumps(res, indent=2) + "\n")


if __name__ == "__main__":
    main()
