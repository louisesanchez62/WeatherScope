#!/usr/bin/env python3
"""Create the WeatherScope collections with their validators and indexes.

Reads schema/<collection>.schema.json and schema/indexes.json. Safe to run
again: existing collections get the current validator (collMod) and missing
indexes are added. Works against a single mongod or a mongos router.

Usage:
    python3 scripts/init_db.py [--uri mongodb://localhost:27017] [--db weatherscope] [--drop]
"""
import argparse
import json
import os
import pathlib

from pymongo import MongoClient

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCHEMA = ROOT / "schema"
COLLECTIONS = ("stations", "observations", "history", "batches")


def load_validator(name: str) -> dict:
    return json.loads((SCHEMA / f"{name}.schema.json").read_text())


def load_indexes() -> dict:
    return json.loads((SCHEMA / "indexes.json").read_text())


def init_db(db, drop: bool = False) -> None:
    indexes = load_indexes()
    existing = set(db.list_collection_names())
    for name in COLLECTIONS:
        validator = load_validator(name)
        if drop and name in existing:
            db.drop_collection(name)
            existing.discard(name)
        opts = {"validator": validator, "validationLevel": "strict", "validationAction": "error"}
        if name in existing:
            db.command("collMod", name, **opts)
            print(f"updated  {name}")
        else:
            db.create_collection(name, **opts)
            print(f"created  {name}")
        for idx in indexes.get(name, []):
            keys = [(field, direction) for field, direction in idx["keys"]]
            db[name].create_index(keys, name=idx["name"])
            print(f"  index  {idx['name']}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--uri", default=os.environ.get("WEATHERSCOPE_URI", "mongodb://localhost:27017"))
    ap.add_argument("--db", default=os.environ.get("WEATHERSCOPE_DB", "weatherscope"))
    ap.add_argument("--drop", action="store_true", help="drop the collections first (deletes their data)")
    args = ap.parse_args()

    client = MongoClient(args.uri, serverSelectionTimeoutMS=5000)
    init_db(client[args.db], drop=args.drop)


if __name__ == "__main__":
    main()
