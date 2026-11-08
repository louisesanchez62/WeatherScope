"""Command-line interface.

    python -m weatherscope stations --area michigan --near -83.35,42.21,25 --element SNWD
    python -m weatherscope f1 USW00094847 --from 2013-12-01 --to 2014-02-28
    python -m weatherscope f1 USW00094847 --from 1991-01-01 --to 2020-12-31 --by winter --element SNWD
    python -m weatherscope trace --station USW00094847 --from 2014-01-05 --to 2014-01-07
    python -m weatherscope trace --id USW00094847:2014-01-06:TMIN --history

Every command accepts --json for machine-readable output, and --uri / --db
(or WEATHERSCOPE_URI / WEATHERSCOPE_DB) to choose the server.
"""
import argparse
import sys

from pymongo.errors import PyMongoError

from . import AREAS, ELEMENTS
from .db import connect
from .f1 import PERIODS, station_summary
from .output import as_json, print_result
from .stations import search_stations
from .trace import trace


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--uri", help="MongoDB URI (default $WEATHERSCOPE_URI or mongodb://localhost:27017)")
    common.add_argument("--db", help="database name (default $WEATHERSCOPE_DB or weatherscope)")
    common.add_argument("--json", action="store_true", help="print the full result as JSON")

    ap = argparse.ArgumentParser(prog="weatherscope", description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="command", required=True)

    s = sub.add_parser("stations", parents=[common], help="F1: search stations in an area")
    s.add_argument("--area", choices=AREAS)
    s.add_argument("--bbox", metavar="LON1,LAT1,LON2,LAT2")
    s.add_argument("--near", metavar="LON,LAT,KM")
    s.add_argument("--element", choices=ELEMENTS, help="only stations that report this element")
    s.add_argument("--name", help="part of the station name")
    s.add_argument("--limit", type=int, default=200)

    f = sub.add_parser("f1", parents=[common], help="F1: summary of one station over a date range")
    f.add_argument("station")
    f.add_argument("--from", dest="start", required=True, metavar="YYYY-MM-DD")
    f.add_argument("--to", dest="end", required=True, metavar="YYYY-MM-DD")
    f.add_argument("--by", choices=PERIODS, default="month")
    f.add_argument("--element", nargs="+", choices=ELEMENTS, default=list(ELEMENTS))

    t = sub.add_parser("trace", parents=[common], help="list the source records behind a result")
    t.add_argument("--id", nargs="+", dest="ids", metavar="STATION:DATE:ELEMENT")
    t.add_argument("--station")
    t.add_argument("--from", dest="start", metavar="YYYY-MM-DD")
    t.add_argument("--to", dest="end", metavar="YYYY-MM-DD")
    t.add_argument("--element", nargs="+", choices=ELEMENTS, default=list(ELEMENTS))
    t.add_argument("--history", action="store_true", help="also list versions replaced by F5")
    t.add_argument("--limit", type=int, default=5000)
    return ap


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        db = connect(args.uri, args.db)
        if args.command == "stations":
            result = search_stations(db, args.area, args.bbox, args.near, args.element, args.name, args.limit)
        elif args.command == "f1":
            result = station_summary(db, args.station, args.start, args.end, args.by, args.element)
        else:
            result = trace(db, args.ids, args.station, args.start, args.end, args.element, args.history, args.limit)
    except (ValueError, LookupError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    except PyMongoError as e:
        print(f"error: the database did not answer ({e.__class__.__name__}: {e})", file=sys.stderr)
        return 3
    print(as_json(result) if args.json else "", end="")
    if not args.json:
        print_result(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
