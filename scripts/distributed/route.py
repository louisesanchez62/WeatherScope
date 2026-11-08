#!/usr/bin/env python3
"""Show shards contacted by a representative observation request."""
import argparse
import json
from scripts.distributed.common import Store, shards_touched

def main() -> None:
    parser = argparse.ArgumentParser(); parser.add_argument("profile", choices=("hashed", "zoned"));
    parser.add_argument("--station"); parser.add_argument("--areas", help="comma-separated areas")
    args = parser.parse_args(); store = Store(args.profile)
    query = store.scope(stations=[args.station] if args.station else None,
                        areas=args.areas.split(",") if args.areas else None,
                        allow_broadcast=False, element="TMAX")
    explain = store.db.command({"explain": {"find": "observations", "filter": query}, "verbosity": "queryPlanner"})
    print(json.dumps({"filter": query, "shards_contacted": shards_touched(explain)}, default=str, indent=2))

if __name__ == "__main__": main()
