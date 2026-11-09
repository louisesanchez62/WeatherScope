#!/usr/bin/env python3
"""E1: increasing data volume on the single server (Part I deployment).

Three real subsets of the archive, all stations, growing in years:
  S1  2011-2020             database e1_s1
  S2  1991-2020             database e1_s2
  S3  1961-2025 (complete)  database weatherscope (already loaded: initial
                            load + F5 import of 2021-2025 + read model)
S1 and S2 are loaded here with F5 and their read model is built, timing both.
Then for each subset: observation counts (TMAX/TMIN/PRCP separately), storage,
whether data and indexes fit in the 3 GB cache, and the same five requests
(bench.E1_REQUESTS), whose answers must be identical across subsets.
Writes results/e1/e1.json.

Usage (local server running, nothing else loading):
    python3 -m scripts.experiments.e1 [--skip-load]
"""
import argparse
import datetime as dt
import json
import pathlib
import subprocess
import time

from scripts.experiments.bench import E1_REQUESTS, machine, storage, time_requests
from weatherscope import bucket
from weatherscope.db import connect
from weatherscope.f5 import import_batch

ROOT = pathlib.Path(__file__).resolve().parents[2]
OUT = ROOT / "results" / "e1" / "e1.json"
SUBSETS = {"S1 2011-2020": ("e1_s1", "2011-01-01", "2020-12-31"),
           "S2 1991-2020": ("e1_s2", "1991-01-01", "2020-12-31"),
           "S3 1961-2025": ("weatherscope", None, None)}


def counts(db) -> dict:
    out = {e: db.observations.count_documents({"element": e}) for e in ("TMAX", "TMIN", "PRCP", "SNOW", "SNWD")}
    out["core_TMAX_TMIN_PRCP"] = out["TMAX"] + out["TMIN"] + out["PRCP"]
    out["total"] = sum(out[e] for e in ("TMAX", "TMIN", "PRCP", "SNOW", "SNWD"))
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--skip-load", action="store_true")
    a = ap.parse_args()
    res = {"generated_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
           "machine": machine(connect()), "subsets": {}}
    for label, (name, start, end) in SUBSETS.items():
        print(f"== {label} ({name})")
        db = connect(db=name)
        entry = {"database": name}
        if start and not a.skip_load:
            subprocess.run(["python3", "scripts/init_db.py", "--db", name, "--drop"], check=True, cwd=ROOT,
                           capture_output=True)
            summary = import_batch(db, f"e1-{name.replace('_', '-')}", start=start, end=end, log=lambda m: None)
            t = time.perf_counter()
            bucket.build(db, log=lambda m: None)
            entry["load"] = {"f5_s": summary["elapsed_s"], "read_model_s": round(time.perf_counter() - t, 1),
                             "inserted": summary["counts"]["inserted"]}
            print(f"  loaded {summary['counts']['inserted']:,} in {summary['elapsed_s']} s, "
                  f"read model {entry['load']['read_model_s']} s")
        if not start:
            ingest = ROOT / "results" / "ingest"
            first = json.loads((ingest / "2026-11-08-initial.json").read_text())
            extra = json.loads((ingest / "2026-11-08-f5-2021-2025.json").read_text())
            part1 = json.loads((ROOT / "results" / "part1" / "compare_designs.json").read_text())
            entry["load"] = {"f5_s": round(first["elapsed_s"] + extra["elapsed_s"], 1),
                             "read_model_s": part1.get("build_s", 612.0),
                             "inserted": first["counts"]["inserted"] + extra["counts"]["inserted"],
                             "note": "measured earlier: initial load 1961-2020 + F5 import 2021-2025 + read model"}
        entry["observations"] = counts(db)
        entry["storage"] = storage(db)
        in_cache_mb = sum(c["data_mb"] + c["index_mb"] for c in entry["storage"].values())
        entry["data_plus_indexes_mb"] = round(in_cache_mb)
        entry["fits_in_3gb_cache"] = in_cache_mb <= 3 * 1024
        entry["requests"] = time_requests(E1_REQUESTS, ["--db", name])
        res["subsets"][label] = entry
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps(res, indent=2) + "\n")
    fps = {req: {s["database"]: s["requests"][req]["fingerprint"] for s in res["subsets"].values()}
           for req in E1_REQUESTS}
    res["same_answers_across_sizes"] = {req: len(set(v.values())) == 1 for req, v in fps.items()}
    OUT.write_text(json.dumps(res, indent=2) + "\n")
    print(json.dumps(res["same_answers_across_sizes"], indent=2))


if __name__ == "__main__":
    main()
