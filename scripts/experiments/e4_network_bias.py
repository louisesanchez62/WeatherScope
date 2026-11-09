#!/usr/bin/env python3
"""E4: does the growth of Michigan's station network bias F4?

Michigan's network grows sharply after 2008 (volunteer CoCoRaHS stations).
F4 counts an event wherever at least one station in a grid cell meets the rule:
more stations means more chances to catch a heavy local snowfall, and newly
covered cells add to the area. Counting events per year over 1991-2020 can
then show a "trend" that comes from the network, not the weather.

Runs F4 (heavy snowfall, >= 150 mm in a day, Michigan, 1991-2020) twice:
  * all stations (default F4);
  * the stable network only (--stable-network: stations whose record spans
    1991-2020), so the same stations are counted every year.
Compares events per year before and after 2008, the stations reporting each
year, and the run time. Writes results/e4/network_bias.json.

Usage:
    python3 scripts/experiments/e4_network_bias.py
"""
import collections
import datetime as dt
import json
import pathlib
import statistics
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from weatherscope.db import connect  # noqa: E402
from weatherscope.f4 import regional_events  # noqa: E402

OUT = ROOT / "results" / "e4" / "network_bias.json"
SPLIT = 2008


def season(day: str) -> int:
    d = dt.date.fromisoformat(day)
    return d.year if d.month >= 7 else d.year - 1  # snow season July-June, labelled by its first year


def reporting_stations(db, stable):
    match = {"area": "michigan", "element": "SNOW", "qflag": None, "mflag": {"$ne": "P"},
             "date": {"$gte": dt.datetime(1991, 7, 1), "$lte": dt.datetime(2020, 6, 30)}}
    if stable:
        match["station"] = {"$in": db.stations.distinct("_id", {"area": "michigan", "long_record": True})}
    out = {}
    for g in db.observations.aggregate([
        {"$match": match},
        {"$group": {"_id": {"y": {"$year": "$date"}, "m": {"$month": "$date"}}, "s": {"$addToSet": "$station"}}},
    ], allowDiskUse=True):
        y = g["_id"]["y"] if g["_id"]["m"] >= 7 else g["_id"]["y"] - 1
        out.setdefault(y, set()).update(g["s"])
    return {y: len(s) for y, s in sorted(out.items())}


def main() -> None:
    db = connect()
    res = {}
    for label, stable in (("all_stations", False), ("stable_network", True)):
        t = time.perf_counter()
        r = regional_events(db, "SNOW", "1991-07-01", "2020-06-30", area="michigan", above=150, stable=stable)
        elapsed = round(time.perf_counter() - t, 1)
        per = collections.defaultdict(collections.Counter)
        for e in r["events"]:
            per[season(e["start"])][e["scope"]] += 1
        stations = reporting_stations(db, stable)
        seasons = list(range(1991, 2020))

        def mean(scope, lo, hi):
            return round(statistics.fmean(per[y][scope] for y in seasons if lo <= y <= hi), 2)
        res[label] = {
            "stations_used": r["stations_used"], "elapsed_s": elapsed,
            "events_by_scope": dict(collections.Counter(e["scope"] for e in r["events"])),
            "per_season_before_2008": {s: mean(s, 1991, SPLIT - 1) for s in ("regional", "local", "isolated")},
            "per_season_from_2008": {s: mean(s, SPLIT, 2019) for s in ("regional", "local", "isolated")},
            "stations_reporting_before_2008": round(statistics.fmean(v for y, v in stations.items() if y < SPLIT), 1),
            "stations_reporting_from_2008": round(statistics.fmean(v for y, v in stations.items() if y >= SPLIT), 1),
            "per_season": {y: dict(per[y]) for y in seasons},
            "stations_reporting": stations,
        }
        print(label, json.dumps({k: v for k, v in res[label].items() if k not in ("per_season", "stations_reporting")}))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(res, indent=2, default=str) + "\n")


if __name__ == "__main__":
    main()
