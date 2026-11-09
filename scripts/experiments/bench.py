#!/usr/bin/env python3
"""Shared timing harness for E1 and E2.

Each request goes through the normal entry point (weatherscope.__main__.execute),
so it is exactly what a user would run, failure guard included. A request is
run once as a warm-up (reported, not counted), then `runs` times; we keep every
time, the median, min and max, whether the answer was complete, and a SHA-256
fingerprint of the canonical answer so that two deployments or two data sizes
can be checked to give the same result.
"""
import hashlib
import json
import platform
import statistics
import subprocess
import time

from weatherscope.__main__ import execute

# E1: the same requests on every data size; all of them fall inside 2011-2020,
# the smallest subset, so the answers must be identical across sizes.
E1_REQUESTS = {
    "F1 Detroit 2011-2020 by month": ["f1", "USW00094847", "--from", "2011-01-01", "--to", "2020-12-31"],
    "F2 heat Germany 2011-2020": ["f2", "--area", "southern_germany", "--element", "TMAX", "--above", "30",
                                  "--min-days", "5", "--from", "2011-01-01", "--to", "2020-12-31"],
    "F3 TMAX 2016-2020 vs 2011-2015": ["f3", "--element", "TMAX", "--from-year", "2016", "--to-year", "2020",
                                       "--ref-from", "2011", "--ref-to", "2015", "--min-ref-years", "4"],
    "F4 heavy snow Michigan 2011-2020": ["f4", "--area", "michigan", "--element", "SNOW", "--above", "150",
                                         "--from", "2011-07-01", "--to", "2020-06-30"],
    "trace Detroit January 2014": ["trace", "--station", "USW00094847", "--from", "2014-01-01", "--to", "2014-01-31"],
}

# E2: a narrow request, long-range aggregations and multi-station analyses.
E2_REQUESTS = {
    "narrow: F1 Detroit one year": ["f1", "USW00094847", "--from", "2014-01-01", "--to", "2014-12-31"],
    "narrow: trace one week": ["trace", "--station", "USW00094847", "--from", "2014-01-01", "--to", "2014-01-07"],
    "long-range: F1 Detroit 60 winters": ["f1", "USW00094847", "--from", "1960-12-01", "--to", "2020-02-29",
                                          "--by", "winter"],
    "long-range: F3 snow cover, both areas": ["f3", "--element", "SNWD", "--months", "12", "1", "2"],
    "long-range: F3 TMAX Germany, raw observations": ["f3", "--element", "TMAX", "--area", "southern_germany",
                                                      "--source", "observations"],
    "multi-station: F2 heat Germany 2003": ["f2", "--area", "southern_germany", "--element", "TMAX", "--above",
                                            "30", "--min-days", "5", "--from", "2003-06-01", "--to", "2003-08-31"],
    "multi-station: F4 heavy snow Michigan 2010-2015": ["f4", "--area", "michigan", "--element", "SNOW",
                                                        "--above", "150", "--from", "2010-11-01", "--to", "2015-03-31"],
}
HEAVY = {"long-range: F3 TMAX Germany, raw observations"}


def fingerprint(result: dict) -> str:
    return hashlib.sha256(json.dumps(result, sort_keys=True, default=str).encode()).hexdigest()[:16]


def time_requests(requests: dict, target: list[str], runs: int = 5, heavy_runs: int = 3, log=print) -> dict:
    """`target` is appended to every request, e.g. ["--profile", "hashed"] or ["--db", "e1_s1"]."""
    out = {}
    for name, argv in requests.items():
        n = heavy_runs if name in HEAVY else runs
        times, fps, complete = [], set(), True
        for i in range(n + 1):
            t = time.perf_counter()
            r = execute(argv + target + ["--json"])
            dt = round(time.perf_counter() - t, 3)
            complete &= bool(r.get("complete"))
            fps.add(fingerprint(r))
            times.append(dt)
        out[name] = {"warmup_s": times[0], "runs_s": times[1:], "median_s": statistics.median(times[1:]),
                     "min_s": min(times[1:]), "max_s": max(times[1:]), "complete": complete,
                     "fingerprint": sorted(fps)[0] if len(fps) == 1 else sorted(fps)}
        log(f"  {name:48} median {out[name]['median_s']:8.3f} s  [{out[name]['min_s']}-{out[name]['max_s']}]"
            f"  warm-up {times[0]} s")
    return out


def machine(db) -> dict:
    mem = subprocess.run(["free", "-b"], capture_output=True, text=True).stdout.split("\n")[1].split()[1]
    status = db.client.admin.command("serverStatus")
    cache = status.get("wiredTiger", {}).get("cache", {}).get("maximum bytes configured")
    return {"platform": platform.platform(), "python": platform.python_version(),
            "cpus": subprocess.run(["nproc"], capture_output=True, text=True).stdout.strip(),
            "ram_gb": round(int(mem) / 2**30, 1), "mongodb": db.client.server_info()["version"],
            "wiredtiger_cache_gb": round(cache / 2**30, 2) if cache else "per shard (see cluster.py)"}


def storage(db) -> dict:
    out = {}
    for c in ("observations", "obs_months"):
        try:
            s = db.command("collStats", c)
        except Exception:
            continue
        out[c] = {"documents": s["count"], "data_mb": round(s["size"] / 2**20, 1),
                  "storage_mb": round(s["storageSize"] / 2**20, 1), "index_mb": round(s["totalIndexSize"] / 2**20, 1)}
    return out
