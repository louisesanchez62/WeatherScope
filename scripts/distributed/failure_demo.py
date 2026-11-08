#!/usr/bin/env python3
"""Run the required safe-failure checks after ``python3 cluster.py kill shardN``.

The script selects a station whose observations belong to the named unavailable
shard, then invokes F1 and a multi-area F3 through the normal mongos endpoint.
Both envelopes must be UNAVAILABLE/complete=false; no partial data are printed
as a successful answer.
"""
import argparse
import json

from scripts.distributed.common import Store, run, shard_status, station_shards
from scripts.services import f1, f3


def main() -> None:
    parser = argparse.ArgumentParser(); parser.add_argument("profile", choices=("hashed", "zoned")); parser.add_argument("shard")
    args = parser.parse_args(); store = Store(args.profile)
    health = shard_status(store)
    if health.get(args.shard, {}).get("up", False):
        raise SystemExit(f"{args.shard} est encore disponible. Exécutez d'abord: python3 cluster.py kill {args.shard.replace('RS', '')}")
    owners = station_shards(store)
    candidates = sorted(station for station, owner in owners.items() if owner == args.shard)
    if not candidates: raise SystemExit(f"aucune station sur {args.shard}")
    station = candidates[0]
    f1_answer = run(store, f1, [station], station=station, start="1991-01-01", end="1991-12-31")
    areas = sorted(store.stations.distinct("area")); required = store.stations.distinct("_id", {"area": {"$in": areas}})
    f3_answer = run(store, f3, required, areas=areas, years=[1991, 1992], element="TMAX")
    print(json.dumps({"failed_shard": args.shard, "station": station, "f1": f1_answer, "f3": f3_answer}, default=str, indent=2))


if __name__ == "__main__": main()
