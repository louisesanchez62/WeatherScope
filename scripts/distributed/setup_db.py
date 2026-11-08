#!/usr/bin/env python3
"""Prepare one distributed WeatherScope database before its first load.

Usage: python3 -m scripts.distributed.setup_db hashed|zoned [--reset]
"""
import argparse
from bson.max_key import MaxKey
from bson.min_key import MinKey
from pymongo import MongoClient
from pymongo.errors import OperationFailure

from scripts.local.init_db import init_db
from scripts.distributed.common import PROFILES

ZONES = {"michigan": "shard1RS", "southern_germany": "shard2RS"}   # valeurs exactes de select_stations.py

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("profile", choices=["hashed", "zoned"])
    ap.add_argument("--reset", action="store_true"); a = ap.parse_args()
    p = PROFILES[a.profile]; ns = f"{p['db']}.observations"
    client = MongoClient(p["uri"], serverSelectionTimeoutMS=5000); admin = client.admin
    if a.reset:
        client.drop_database(p["db"])
    # Metadata is small and deliberately lives on shard1. Observations are the
    # only large collection and the only one sharded.  Do not use init_db(...,
    # drop=True) after sharding: it would destroy the sharded collection.
    try:
        admin.command("enableSharding", p["db"], primaryShard="shard1RS")
    except OperationFailure as exc:
        if exc.code not in (20, 23): raise       # already enabled / initialized
    init_db(client[p["db"]], drop=False)
    if a.profile == "hashed":
        # Hashed station key balances storage and writes; exact-station F1 is
        # targeted, whereas regional/date requests are scatter-gather.
        try:
            admin.command("shardCollection", ns, key={"station": "hashed"}, numInitialChunks=12)
        except OperationFailure as exc:
            if exc.code not in (20, 23): raise
    else:
        # Area+station keeps every area's observations co-located.  Zones turn
        # that logical policy into an explicit shard allocation.
        for zone, shard in ZONES.items():
            admin.command("addShardToZone", shard, zone=zone)
        try:
            admin.command("shardCollection", ns, key={"area": 1, "station": 1})
        except OperationFailure as exc:
            if exc.code not in (20, 23): raise
        for zone in ZONES:
            admin.command("updateZoneKeyRange", ns, zone=zone,
                          min={"area": zone, "station": MinKey()},
                          max={"area": zone, "station": MaxKey()})
    cfg = client["config"]; uuid = cfg["collections"].find_one({"_id": ns})["uuid"]
    for r in cfg["chunks"].aggregate([{"$match": {"uuid": uuid}}, {"$group": {"_id": "$shard", "chunks": {"$sum": 1}}}]):
        print(r)

if __name__ == "__main__": main()
