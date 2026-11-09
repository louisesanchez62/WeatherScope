#!/usr/bin/env python3
"""Part I: compare the main data model with the alternative (bucket) model.

Main model:  one document per observation (`observations`).
Alternative: one document per station, element and month (`obs_months`),
             built from the same data by weatherscope.bucket.build().

Steps:
  1. (--build) build `obs_months` from `observations`, timing it;
  2. storage: documents, data size, storage size, index size of both;
  3. equivalence: both models must give identical answers to
       R1  F1 by month, Detroit Metro, 1961-2020, five elements
       R2  F3 station-months, snow depth, both areas, 1961-2020
     (nothing is timed if they differ);
  4. timing: R1 and R2 on each model, runs interleaved (A, B, A, B, ...);
     the first run of each is a warm-up and is reported separately.

Raw measurements go to results/part1/compare_designs.json.

Usage:
    python3 scripts/compare_designs.py [--build] [--runs-r1 7] [--runs-r2 3]
"""
import argparse
import datetime as dt
import json
import pathlib
import platform
import statistics
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from weatherscope import AREAS, ELEMENTS, bucket  # noqa: E402
from weatherscope.db import connect  # noqa: E402
from weatherscope.f1 import station_summary  # noqa: E402
from weatherscope.f3 import _station_months  # noqa: E402

OUT = ROOT / "results" / "part1" / "compare_designs.json"
STATION = "USW00094847"
KEYS = ("element", "period", "expected_days", "present", "usable", "coverage", "valid", "qflagged",
        "mflag_P", "mflag_T", "mean", "min", "max", "total", "days_positive")


def r1_main(db):
    return station_summary(db, STATION, "1961-01-01", "2020-12-31", "month", ELEMENTS)["periods"]


def r1_bucket(db):
    return bucket.f1_months(db, STATION, 1961, 2020, ELEMENTS)


def r2_main(db):
    return _station_months(db, AREAS, "SNWD", 1961, 2020)


def r2_bucket(db):
    return bucket.station_months(db, AREAS, "SNWD", 1961, 2020)


def norm_r1(rows):
    return sorted(tuple(r.get(k) for k in KEYS) for r in rows)


def same_r2(a, b):
    if a.keys() != b.keys():
        return False, f"{len(a.keys() ^ b.keys())} station-months differ"
    worst = max(abs(a[k] - b[k]) for k in a)
    return worst < 1e-9, f"largest difference {worst:g}"


def coll_stats(db, name):
    s = db.command("collStats", name)
    return {"documents": s["count"], "data_mb": round(s["size"] / 2**20, 1),
            "storage_mb": round(s["storageSize"] / 2**20, 1), "index_mb": round(s["totalIndexSize"] / 2**20, 1),
            "avg_doc_bytes": s.get("avgObjSize")}


def timed(fn, db):
    t = time.perf_counter()
    fn(db)
    return round(time.perf_counter() - t, 3)


def summary(times):
    return {"warmup_s": times[0], "runs_s": times[1:], "median_s": statistics.median(times[1:]),
            "min_s": min(times[1:]), "max_s": max(times[1:])}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--build", action="store_true", help="(re)build obs_months first")
    ap.add_argument("--runs-r1", type=int, default=7, help="runs per model for R1, warm-up included")
    ap.add_argument("--runs-r2", type=int, default=3, help="runs per model for R2, warm-up included")
    args = ap.parse_args()
    db = connect()
    res = {"generated_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
           "machine": {"python": platform.python_version(), "platform": platform.platform(),
                       "mongodb": db.client.server_info()["version"],
                       "wiredtiger_cache_gb": round(db.client.admin.command("serverStatus")["wiredTiger"]["cache"]
                                                    ["maximum bytes configured"] / 2**30, 2)}}

    if args.build:
        t = time.perf_counter()
        bucket.build(db)
        res["build_s"] = round(time.perf_counter() - t, 1)
        print(f"built obs_months in {res['build_s']} s")

    res["storage"] = {"observations": coll_stats(db, "observations"), "obs_months": coll_stats(db, bucket.COLL)}
    print(json.dumps(res["storage"], indent=2))

    ok1 = norm_r1(r1_main(db)) == norm_r1(r1_bucket(db))
    ok2, detail2 = same_r2(r2_main(db), r2_bucket(db))
    res["equivalence"] = {"R1_f1_detroit_by_month": ok1, "R2_f3_station_months_snwd": ok2, "R2_detail": detail2}
    print("equivalence:", res["equivalence"])
    if not (ok1 and ok2):
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps(res, indent=2) + "\n")
        raise SystemExit("the two models disagree: no timing")

    for name, runs, fa, fb in (("R1", args.runs_r1, r1_main, r1_bucket), ("R2", args.runs_r2, r2_main, r2_bucket)):
        ta, tb = [], []
        for i in range(runs):
            ta.append(timed(fa, db))
            tb.append(timed(fb, db))
            print(f"  {name} run {i + 1}/{runs}: observations {ta[-1]} s, obs_months {tb[-1]} s")
        res[name] = {"observations": summary(ta), "obs_months": summary(tb)}

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(res, indent=2) + "\n")
    for name in ("R1", "R2"):
        a, b = res[name]["observations"], res[name]["obs_months"]
        print(f"{name}: observations median {a['median_s']} s [{a['min_s']}-{a['max_s']}], "
              f"obs_months median {b['median_s']} s [{b['min_s']}-{b['max_s']}]")


if __name__ == "__main__":
    main()
