#!/usr/bin/env python3
"""Copy the local logical database to a prepared distributed profile.

This is a migration/verification utility, not a way to generate benchmark
data.  It streams documents in bounded batches and uses idempotent upserts, so
re-running it neither duplicates observations nor changes their identity.

Usage: python3 -m scripts.distributed.sync_from_local hashed|zoned [--clear]
"""
import argparse
from pymongo import MongoClient, ReplaceOne

from weatherscope.deploy import PROFILES

COLLECTIONS = ("stations", "observations", "history", "batches", "obs_months")


def copy_collection(source, target, name: str, clear: bool) -> int:
    if clear:
        target[name].delete_many({})
    operations, total = [], 0
    for document in source[name].find({}, no_cursor_timeout=True):
        operations.append(ReplaceOne({"_id": document["_id"]}, document, upsert=True))
        if len(operations) == 1000:
            target[name].bulk_write(operations, ordered=False)
            total += len(operations); operations.clear()
    if operations:
        target[name].bulk_write(operations, ordered=False); total += len(operations)
    return total


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("profile", choices=("hashed", "zoned"))
    parser.add_argument("--clear", action="store_true", help="empty target collections first (destructive)")
    parser.add_argument("--source-db", default=PROFILES["local"]["db"])
    parser.add_argument("--target-db", help="default: the profile's database")
    args = parser.parse_args()
    source = MongoClient(PROFILES["local"]["uri"], serverSelectionTimeoutMS=5000)[args.source_db]
    target = MongoClient(PROFILES[args.profile]["uri"], w="majority",
                         serverSelectionTimeoutMS=5000)[args.target_db or PROFILES[args.profile]["db"]]
    for name in COLLECTIONS:
        print(f"{name}: {copy_collection(source, target, name, args.clear)} document(s) copied")


if __name__ == "__main__":
    main()
