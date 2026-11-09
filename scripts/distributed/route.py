#!/usr/bin/env python3
"""Show which shards answer representative requests (explain through mongos).

The filters are the ones the services build: F1 targets one station (and its
area), F2 an area's stations, F3 reads an area's months in the read model.

Usage:
    python3 -m scripts.distributed.route hashed|zoned [--db NAME]
"""
import argparse
import datetime as dt
import json

from weatherscope.deploy import profile_db, shards_touched

REQUESTS = {
    "F1, one station, one year": ("observations", {
        "area": "michigan", "station": "USW00094847", "element": {"$in": ["TMAX", "TMIN"]},
        "date": {"$gte": dt.datetime(2014, 1, 1), "$lte": dt.datetime(2014, 12, 31)}}),
    "F2, southern Germany, one summer": ("observations", {
        "area": {"$in": ["southern_germany"]}, "element": "TMAX", "qflag": None,
        "date": {"$gte": dt.datetime(2003, 6, 1), "$lte": dt.datetime(2003, 8, 31)}}),
    "F3, Michigan months 1961-2020": ("obs_months", {
        "area": {"$in": ["michigan"]}, "element": "SNWD", "year": {"$gte": 1961, "$lte": 2020}}),
    "F3, both areas, months 1961-2020": ("obs_months", {
        "area": {"$in": ["michigan", "southern_germany"]}, "element": "SNWD", "year": {"$gte": 1961, "$lte": 2020}}),
}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("profile", choices=("hashed", "zoned"))
    ap.add_argument("--db")
    a = ap.parse_args()
    db = profile_db(a.profile, a.db)
    out = {}
    for label, (coll, flt) in REQUESTS.items():
        plan = db.command({"explain": {"find": coll, "filter": flt}, "verbosity": "queryPlanner"})
        out[label] = shards_touched(plan)
        print(f"{label:38} {coll:13} -> {', '.join(out[label])}")
    print(json.dumps({"profile": a.profile, "shards_contacted": out}, indent=2))


if __name__ == "__main__":
    main()
