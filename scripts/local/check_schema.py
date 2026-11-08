#!/usr/bin/env python3
"""Check the schema against the example documents.

Creates a throwaway database with the real validators, then:
  * every file in schema/examples/valid/ must be accepted;
  * every file in schema/examples/invalid/ must be rejected;
  * every valid observation must have _id == "<station>:<YYYY-MM-DD>:<element>"
    (MongoDB validators cannot compare two fields, so this is checked here and
    must also be enforced by the loader).

Example files are named <collection>.<label>.json and written in MongoDB
Extended JSON ({"$date": ...}, {"$oid": ...}). The database is dropped at the end.

Usage:
    python3 scripts/check_schema.py [--uri mongodb://localhost:27017]
"""
import argparse
import os
import pathlib
import sys

from bson import json_util
from pymongo import MongoClient
from pymongo.errors import WriteError

from scripts.local.init_db import ROOT, init_db

EXAMPLES = ROOT / "schema" / "examples"
CHECK_DB = "weatherscope_schema_check"


def observation_id(doc: dict) -> str:
    return f"{doc['station']}:{doc['date'].strftime('%Y-%m-%d')}:{doc['element']}"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--uri", default=os.environ.get("WEATHERSCOPE_URI", "mongodb://localhost:27017"))
    args = ap.parse_args()

    client = MongoClient(args.uri, serverSelectionTimeoutMS=5000)
    db = client[CHECK_DB]
    failures = 0
    try:
        init_db(db, drop=True)
        print()
        for expect_ok, folder in ((True, "valid"), (False, "invalid")):
            for path in sorted((EXAMPLES / folder).glob("*.json")):
                collection = path.name.split(".", 1)[0]
                doc = json_util.loads(path.read_text())
                try:
                    db[collection].insert_one(doc)
                    accepted, reason = True, ""
                except WriteError as e:
                    accepted, reason = False, e.details.get("errmsg", str(e)) if e.details else str(e)
                    db[collection].delete_many({"_id": doc.get("_id")})
                ok = accepted == expect_ok
                if ok and expect_ok and collection == "observations" and doc["_id"] != observation_id(doc):
                    ok, reason = False, f"_id should be {observation_id(doc)}"
                if not ok:
                    failures += 1
                if expect_ok and accepted:
                    db[collection].delete_many({"_id": doc["_id"]})
                verdict = "accepted" if accepted else "rejected"
                print(f"{'ok  ' if ok else 'FAIL'} {folder:7} {verdict:8} {path.name}" + (f"  ({reason})" if not ok else ""))
    finally:
        client.drop_database(CHECK_DB)

    print(f"\n{'all checks passed' if failures == 0 else f'{failures} check(s) failed'}")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
