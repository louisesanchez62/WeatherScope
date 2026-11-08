#!/usr/bin/env python3
"""Automated local/distributed equivalence check with canonical ordering.

It checks a narrow F1 and a multi-area, multi-year F3.  The latter necessarily
spans the two zoned shards; all service results are canonical JSON so ties are
deterministic before comparison.
"""
import argparse
import json
from datetime import date, datetime

from scripts.distributed.common import Store, run
from scripts.services import f1, f3


def canonical(value):
    if isinstance(value, (datetime, date)): return value.isoformat()
    if isinstance(value, dict): return {str(k): canonical(value[k]) for k in sorted(value)}
    if isinstance(value, list): return [canonical(item) for item in value]
    if isinstance(value, float): return round(value, 10)
    return value


def comparable(value):
    if isinstance(value, dict):
        return {key: comparable(item) for key, item in value.items() if key != "profile"}
    if isinstance(value, list):
        return [comparable(item) for item in value]
    return value


def requests(store):
    stations = sorted(store.stations.distinct("_id"))
    if not stations:
        raise RuntimeError(f"base vide pour le profil {store.name} ({store.db_name})")
    areas = sorted(store.stations.distinct("area"))
    one = stations[0]
    f1_result = run(store, f1, [one], station=one, start="1991-01-01", end="1991-12-31")
    required = store.stations.distinct("_id", {"area": {"$in": areas}})
    f3_result = run(store, f3, required, areas=areas, years=[1991, 1992], element="TMAX")
    return {"f1_narrow": f1_result, "f3_multi_area": f3_result}


def main() -> None:
    parser = argparse.ArgumentParser(); parser.add_argument("profiles", nargs="*", default=["hashed", "zoned"])
    args = parser.parse_args(); local = comparable(canonical(requests(Store("local")))); failures = 0
    for profile in args.profiles:
        actual = comparable(canonical(requests(Store(profile))))
        ok = actual == local
        print(f"{profile}: {'OK' if ok else 'DIFFERENT'}")
        if not ok:
            failures += 1
            (open(f"/tmp/weatherscope-{profile}-comparison.json", "w")).write(json.dumps({"local": local, "actual": actual}, indent=2))
    raise SystemExit(1 if failures else 0)


if __name__ == "__main__": main()
