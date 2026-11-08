#!/usr/bin/env python3
"""Two checks on *why* snow cover is shrinking, run on the stored data.

1. Persistence (Michigan only: snowfall and snow depth are both measured).
   For each station with complete snowfall and snow-depth records in both
   periods, winters (Dec-Feb) that pass the 3/5 rule for both elements are
   pooled per period, and we compute snow-cover days per 10 cm of snowfall.
   If snow falls as much but covers the ground for fewer days, persistence
   drops.

2. Near-zero days, both areas. For stations with complete TMAX and TMIN in
   both periods, the share of winter days whose daily mean (TMAX+TMIN)/2 lies
   between -2 and +2 degC. Snow cover is most fragile on those days: a small
   warming turns them into thaw or rain.

Uses the F1 service for (1) and one MongoDB aggregation per area for (2).
Writes results/winter/mechanisms.json.

Usage:
    python3 scripts/winter_mechanisms.py
"""
import csv
import datetime as dt
import json
import pathlib
import statistics
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from weatherscope.db import connect  # noqa: E402
from weatherscope.f1 import station_summary  # noqa: E402

COVERAGE = ROOT / "results" / "coverage" / "stations.csv"
OUT = ROOT / "results" / "winter" / "mechanisms.json"
PERIODS = {"1961-1990": (1961, 1989), "1991-2020": (1990, 2019)}  # winters labelled by their December
FULL = 288  # valid months out of 360


def complete(row, elements):
    return all(int(row.get(f"{p}_{e}_valid_months") or 0) >= FULL for p in ("study", "reference") for e in elements)


def persistence(db, stations):
    out = {}
    per_station = []
    for sid in stations:
        r = station_summary(db, sid, "1961-12-01", "2020-02-29", "winter", ["SNOW", "SNWD"])
        snow = {x["period"]: x for x in r["periods"] if x["element"] == "SNOW" and x["valid"]}
        cover = {x["period"]: x for x in r["periods"] if x["element"] == "SNWD" and x["valid"]}
        rec = {"station": sid, "name": r["station"]["name"]}
        for label, (lo, hi) in PERIODS.items():
            winters = [w for w in snow if w in cover and lo <= int(w[:4]) <= hi]
            mm = sum(snow[w]["total"] or 0 for w in winters)
            days = sum(cover[w]["days_positive"] for w in winters)
            rec[label] = {"winters": len(winters), "snowfall_mm": round(mm), "cover_days": days,
                          "days_per_10cm": round(days / (mm / 100), 2) if mm else None}
        per_station.append(rec)
    usable = [s for s in per_station if all(s[p]["winters"] >= 20 and s[p]["days_per_10cm"] for p in PERIODS)]
    for label in PERIODS:
        out[label] = {
            "stations": len(usable),
            "mean_snowfall_mm_per_winter": round(statistics.fmean(s[label]["snowfall_mm"] / s[label]["winters"] for s in usable)),
            "mean_cover_days_per_winter": round(statistics.fmean(s[label]["cover_days"] / s[label]["winters"] for s in usable), 1),
            "mean_cover_days_per_10cm": round(statistics.fmean(s[label]["days_per_10cm"] for s in usable), 2),
        }
    out["stations_lower_persistence"] = sum(1 for s in usable if s["1991-2020"]["days_per_10cm"] < s["1961-1990"]["days_per_10cm"])
    out["per_station"] = usable
    return out


def near_zero(db, area, stations):
    pipeline = [
        {"$match": {"area": area, "station": {"$in": stations}, "element": {"$in": ["TMAX", "TMIN"]},
                    "date": {"$gte": dt.datetime(1961, 12, 1), "$lte": dt.datetime(2020, 2, 29)}, "qflag": None}},
        {"$match": {"$expr": {"$in": [{"$month": "$date"}, [12, 1, 2]]}}},
        {"$group": {"_id": {"s": "$station", "d": "$date"}, "n": {"$sum": 1}, "sum": {"$sum": "$value"}}},
        {"$match": {"n": 2}},
        {"$project": {"mean": {"$divide": ["$sum", 2]},
                      "winter": {"$cond": [{"$eq": [{"$month": "$_id.d"}, 12]}, {"$year": "$_id.d"},
                                           {"$subtract": [{"$year": "$_id.d"}, 1]}]}}},
        {"$group": {"_id": {"$cond": [{"$lte": ["$winter", 1989]}, "1961-1990", "1991-2020"]},
                    "days": {"$sum": 1},
                    "near_zero": {"$sum": {"$cond": [{"$and": [{"$gte": ["$mean", -2]}, {"$lte": ["$mean", 2]}]}, 1, 0]}},
                    "below": {"$sum": {"$cond": [{"$lt": ["$mean", -2]}, 1, 0]}},
                    "above": {"$sum": {"$cond": [{"$gt": ["$mean", 2]}, 1, 0]}},
                    "mean": {"$avg": "$mean"}}},
    ]
    out = {"stations": len(stations)}
    for g in db.observations.aggregate(pipeline, allowDiskUse=True):
        n = g["days"]
        out[g["_id"]] = {"days": n, "mean_daily_temp": round(g["mean"], 2),
                         "share_below_-2": round(g["below"] / n, 3),
                         "share_-2_to_+2": round(g["near_zero"] / n, 3),
                         "share_above_+2": round(g["above"] / n, 3)}
    return out


def main() -> None:
    db = connect()
    with COVERAGE.open() as f:
        rows = list(csv.DictReader(f))
    snow_st = [r["id"] for r in rows if r["area"] == "michigan" and complete(r, ["SNOW", "SNWD"])]
    temp_st = {a: [r["id"] for r in rows if r["area"] == a and complete(r, ["TMAX", "TMIN"])]
               for a in ("michigan", "southern_germany")}
    result = {
        "generated_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "persistence_michigan": persistence(db, snow_st),
        "near_zero": {a: near_zero(db, a, ids) for a, ids in temp_st.items()},
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, indent=2) + "\n")
    summary = {k: v for k, v in result["persistence_michigan"].items() if k != "per_station"}
    print(json.dumps({"persistence_michigan": summary, "near_zero": result["near_zero"]}, indent=2))


if __name__ == "__main__":
    main()
