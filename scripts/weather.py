#!/usr/bin/env python3
"""Uniform command-line interface for local and distributed WeatherScope.

Examples:
  python3 -m scripts.weather --profile local f1 USW00014822 2010-01-01 2010-01-31
  python3 -m scripts.weather --profile zoned f3 michigan,southern_germany 2018,2019 TMAX
"""
import argparse
import json

from scripts.distributed.common import Store, run
from scripts.services import f1, f3


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--profile", choices=("local", "hashed", "zoned"), default="local")
    sub = parser.add_subparsers(dest="command", required=True)
    one = sub.add_parser("f1"); one.add_argument("station"); one.add_argument("start"); one.add_argument("end")
    three = sub.add_parser("f3"); three.add_argument("areas", help="comma-separated areas")
    three.add_argument("years", help="comma-separated years"); three.add_argument("element", choices=("TMAX", "TMIN", "PRCP"))
    args = parser.parse_args(); store = Store(args.profile)
    if args.command == "f1":
        result = run(store, f1, [args.station], station=args.station, start=args.start, end=args.end)
    else:
        areas = sorted(set(args.areas.split(","))); years = sorted({int(y) for y in args.years.split(",")})
        required = store.stations.distinct("_id", {"area": {"$in": areas}})
        result = run(store, f3, required, areas=areas, years=years, element=args.element)
    print(json.dumps(result, default=str, sort_keys=True, separators=(",", ":")))


if __name__ == "__main__":
    main()
