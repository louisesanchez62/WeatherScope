#!/usr/bin/env python3
"""Prepare one distributed WeatherScope database before its first load.

Creates the collections, validators and indexes (same as Part I), then shards
the two large collections, `observations` and the monthly read model
`obs_months`, with the profile's key:

  hashed  {station: "hashed"}: balances storage and writes; one station is
          targeted, area and date requests go to every shard.
  zoned   {area: 1, station: 1} with zones: Michigan on shard1RS, southern
          Germany on shard2RS; area requests are targeted.

Both collections use the same key, so a station's observations and its months
live on the same shard. `stations`, `history` and `batches` are small and stay
whole on the primary shard (shard1RS).

Usage (from the repository root, cluster running):
    python3 -m scripts.distributed.setup_db hashed|zoned [--db NAME] [--reset]
"""
import argparse

from bson.max_key import MaxKey
from bson.min_key import MinKey
from pymongo import MongoClient
from pymongo.errors import OperationFailure

from scripts.init_db import init_db
from weatherscope.deploy import PROFILES, SHARDED

ZONES = {"michigan": "shard1RS", "southern_germany": "shard2RS"}  # area values from select_stations.py
# The two zones cover the whole key space, so no chunk is left outside a zone. A zone that stopped at
# {area: X, station: MaxKey} would leave a chunk starting at that key on another shard, and an
# area-only query (inclusive upper bound) would then contact two shards instead of one.
ZONE_RANGES = {
    "michigan": ({"area": MinKey(), "station": MinKey()}, {"area": "southern_germany", "station": MinKey()}),
    "southern_germany": ({"area": "southern_germany", "station": MinKey()}, {"area": MaxKey(), "station": MaxKey()}),
}


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("profile", choices=["hashed", "zoned"])
    ap.add_argument("--db", help="database name (default: the profile's)")
    ap.add_argument("--reset", action="store_true", help="drop the database first")
    a = ap.parse_args()
    p = PROFILES[a.profile]
    name = a.db or p["db"]
    client = MongoClient(p["uri"], serverSelectionTimeoutMS=5000)
    admin = client.admin
    if a.reset:
        client.drop_database(name)
    try:
        admin.command("enableSharding", name, primaryShard="shard1RS")
    except OperationFailure as exc:
        if exc.code not in (20, 23):  # already enabled
            raise
    # Do not use init_db(..., drop=True) after sharding: it would destroy the sharded collections.
    init_db(client[name], drop=False)
    if a.profile == "zoned":
        for zone, shard in ZONES.items():
            admin.command("addShardToZone", shard, zone=zone)
    for coll in SHARDED:
        ns = f"{name}.{coll}"
        key = {"station": "hashed"} if a.profile == "hashed" else {"area": 1, "station": 1}
        try:
            if a.profile == "hashed":
                admin.command("shardCollection", ns, key=key, numInitialChunks=12)
            else:
                admin.command("shardCollection", ns, key=key)
        except OperationFailure as exc:
            if exc.code not in (20, 23):  # already sharded
                raise
        if a.profile == "zoned":
            for zone, (lo, hi) in ZONE_RANGES.items():
                admin.command("updateZoneKeyRange", ns, zone=zone, min=lo, max=hi)
        uuid = client.config.collections.find_one({"_id": ns})["uuid"]
        chunks = {r["_id"]: r["n"] for r in client.config.chunks.aggregate(
            [{"$match": {"uuid": uuid}}, {"$group": {"_id": "$shard", "n": {"$sum": 1}}}])}
        print(f"{ns}: key {key}, chunks per shard {chunks}")


if __name__ == "__main__":
    main()
