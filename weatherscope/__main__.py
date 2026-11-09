"""Command-line interface.

    python -m weatherscope stations --area michigan --near -83.35,42.21,25 --element SNWD
    python -m weatherscope f1 USW00094847 --from 2013-12-01 --to 2014-02-28
    python -m weatherscope f1 USW00094847 --from 1991-01-01 --to 2020-12-31 --by winter --element SNWD
    python -m weatherscope f2 --area southern_germany --element TMAX --above 30 --min-days 5 --from 2003-06-01 --to 2003-08-31
    python -m weatherscope trace --station USW00094847 --from 2014-01-05 --to 2014-01-07
    python -m weatherscope trace --id USW00094847:2014-01-06:TMIN --history

Every command accepts --json for machine-readable output, and either
--profile local|hashed|zoned or --uri / --db (or WEATHERSCOPE_URI /
WEATHERSCOPE_DB) to choose the deployment. On a cluster, a request whose
data sit on an unreachable shard is refused (status UNAVAILABLE, exit code 4)
instead of returning a partial answer.
"""
import argparse
import sys

from pymongo.errors import PyMongoError

from . import AREAS, ELEMENTS
from .db import connect
from .deploy import PROFILES, guard, profile_db
from .f1 import PERIODS, station_summary
from .f2 import find_episodes
from .f3 import monthly_anomalies
from .f4 import regional_events
from .f5 import import_batch
from .output import as_json, print_result
from .stations import search_stations
from .trace import trace


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--uri", help="MongoDB URI (default $WEATHERSCOPE_URI or mongodb://localhost:27017)")
    common.add_argument("--db", help="database name (default $WEATHERSCOPE_DB or weatherscope)")
    common.add_argument("--json", action="store_true", help="print the full result as JSON")
    common.add_argument("--profile", choices=list(PROFILES),
                        help="deployment: local (one mongod), hashed or zoned (cluster through mongos); "
                             "sets the URI and database (--db still overrides the database)")

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

    e = sub.add_parser("f2", parents=[common], help="F2: episodes of consecutive days meeting a rule")
    e.add_argument("--element", required=True, choices=ELEMENTS)
    e.add_argument("--from", dest="start", required=True, metavar="YYYY-MM-DD")
    e.add_argument("--to", dest="end", required=True, metavar="YYYY-MM-DD")
    e.add_argument("--area", choices=AREAS)
    e.add_argument("--bbox", metavar="LON1,LAT1,LON2,LAT2")
    e.add_argument("--near", metavar="LON,LAT,KM")
    e.add_argument("--station", nargs="+", dest="stations", metavar="ID")
    rule = e.add_mutually_exclusive_group(required=True)
    rule.add_argument("--above", type=float, help="value >= X (degC or mm)")
    rule.add_argument("--below", type=float, help="value <= X (degC or mm)")
    rule.add_argument("--pct-above", type=float, metavar="P", help="above the station's 1961-1990 P-th percentile")
    rule.add_argument("--pct-below", type=float, metavar="P", help="below the station's 1961-1990 (100-P)-th percentile")
    e.add_argument("--min-days", type=int, default=1)
    e.add_argument("--top", type=int, default=20, help="episodes shown in the table (default 20; --json gives all)")

    a = sub.add_parser("f3", parents=[common], help="F3: most unusual months per area, against a reference")
    a.add_argument("--element", required=True, choices=ELEMENTS)
    a.add_argument("--area", nargs="+", choices=AREAS, default=list(AREAS))
    a.add_argument("--from-year", type=int, default=1991)
    a.add_argument("--to-year", type=int, default=2020)
    a.add_argument("--ref-from", type=int, default=1961, help="first reference year (default 1961)")
    a.add_argument("--ref-to", type=int, default=1990, help="last reference year (default 1990)")
    a.add_argument("--months", nargs="+", type=int, choices=range(1, 13), metavar="M", help="only these calendar months, e.g. 12 1 2")
    a.add_argument("--grid", type=float, default=1.0, help="grid cell size in degrees (default 1)")
    a.add_argument("--min-ref-years", type=int, default=24, help="valid reference years needed for a normal (default 24)")
    a.add_argument("--min-cells", type=float, default=0.5, help="share of the area's cells needed to rank a month (default 0.5)")
    a.add_argument("--sign", choices=["high", "low"], help="rank only warm/wet (high) or cold/dry (low) months")
    a.add_argument("--top", type=int, default=10)
    a.add_argument("--max-elev", type=float, help="only stations at or below this elevation (m)")
    a.add_argument("--source", choices=["auto", "buckets", "observations"], default="auto",
                   help="read station-months from the monthly read model (default when built) or from observations")

    r = sub.add_parser("f4", parents=[common], help="F4: regional events versus isolated extremes")
    r.add_argument("--element", required=True, choices=ELEMENTS)
    r.add_argument("--from", dest="start", required=True, metavar="YYYY-MM-DD")
    r.add_argument("--to", dest="end", required=True, metavar="YYYY-MM-DD")
    r.add_argument("--area", choices=AREAS)
    r.add_argument("--bbox", metavar="LON1,LAT1,LON2,LAT2")
    r.add_argument("--near", metavar="LON,LAT,KM")
    r.add_argument("--station", nargs="+", dest="stations", metavar="ID")
    rule4 = r.add_mutually_exclusive_group(required=True)
    rule4.add_argument("--above", type=float)
    rule4.add_argument("--below", type=float)
    rule4.add_argument("--pct-above", type=float, metavar="P")
    rule4.add_argument("--pct-below", type=float, metavar="P")
    r.add_argument("--tolerance", type=int, default=1, help="days of offset allowed between stations (default 1)")
    r.add_argument("--grid", type=float, default=1.0, help="grid cell size in degrees (default 1)")
    r.add_argument("--min-share", type=float, default=0.25, help="share of reporting cells affected (default 0.25)")
    r.add_argument("--min-cells", type=int, default=2, help="affected cells needed (default 2)")
    r.add_argument("--top", type=int, default=10)

    u = sub.add_parser("f5", parents=[common], help="F5: import a batch of NOAA observations")
    u.add_argument("--label", required=True, help="batch label (lowercase, digits, dashes)")
    u.add_argument("--files", nargs="+", help="GHCN CSV files, per station or per year (default: downloaded by_station files)")
    u.add_argument("--station", nargs="+", dest="stations", metavar="ID")
    u.add_argument("--long-only", action="store_true")
    u.add_argument("--include-unselected", action="store_true",
                   help="also accept stations of our areas left out of the initial load (e.g. above 800 m)")
    u.add_argument("--from", dest="start", default="1961-01-01", metavar="YYYY-MM-DD")
    u.add_argument("--to", dest="end", default="2020-12-31", metavar="YYYY-MM-DD")

    t = sub.add_parser("trace", parents=[common], help="list the source records behind a result")
    t.add_argument("--id", nargs="+", dest="ids", metavar="STATION:DATE:ELEMENT")
    t.add_argument("--station")
    t.add_argument("--from", dest="start", metavar="YYYY-MM-DD")
    t.add_argument("--to", dest="end", metavar="YYYY-MM-DD")
    t.add_argument("--element", nargs="+", choices=ELEMENTS, default=list(ELEMENTS))
    t.add_argument("--history", action="store_true", help="also list versions replaced by F5")
    t.add_argument("--limit", type=int, default=5000)
    return ap


def open_db(args):
    if args.profile:
        return profile_db(args.profile, args.db)
    return connect(args.uri, args.db)


def run_command(db, args) -> dict:
    """Run one parsed command on a database handle and return its result (no printing)."""
    if args.command == "stations":
        return search_stations(db, args.area, args.bbox, args.near, args.element, args.name, args.limit)
    if args.command == "f1":
        return station_summary(db, args.station, args.start, args.end, args.by, args.element)
    if args.command == "f2":
        result = find_episodes(db, args.element, args.start, args.end, args.area, args.bbox, args.near,
                               args.stations, args.above, args.below, args.pct_above, args.pct_below,
                               args.min_days)
        result["display_top"] = args.top
        return result
    if args.command == "f3":
        return monthly_anomalies(db, args.element, args.area, args.from_year, args.to_year, args.ref_from,
                                 args.ref_to, args.months, args.grid, args.min_ref_years, args.min_cells,
                                 args.sign, args.top, args.max_elev, source=args.source)
    if args.command == "f4":
        return regional_events(db, args.element, args.start, args.end, args.area, args.bbox, args.near,
                               args.stations, args.above, args.below, args.pct_above, args.pct_below,
                               args.tolerance, args.grid, args.min_share, args.min_cells, args.top)
    if args.command == "f5":
        return import_batch(db, args.label, args.files, args.stations, args.long_only, args.start, args.end,
                            args.include_unselected, log=lambda m: print(m, file=sys.stderr))
    return trace(db, args.ids, args.station, args.start, args.end, args.element, args.history, args.limit)


def execute(argv) -> dict:
    """Parse, guard and run a command; always returns an answer with `complete` (used by the scripts)."""
    args = build_parser().parse_args(argv)
    db = open_db(args)
    refused = guard(db, args.command, args)
    if refused:
        return refused
    try:
        return run_command(db, args)
    except PyMongoError as e:  # a shard failed during the request: never a partial answer
        return {"service": args.command, "status": "ERROR", "complete": False,
                "message": f"the database did not answer ({e.__class__.__name__}: {e})"}


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        db = open_db(args)
        refused = guard(db, args.command, args)
        if refused:
            print(as_json(refused) if args.json else f"UNAVAILABLE: {refused['message']}\n"
                  + as_json(refused["missing"]))
            return 4
        result = run_command(db, args)
    except (ValueError, LookupError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    except PyMongoError as e:
        failed = {"service": args.command, "status": "ERROR", "complete": False,
                  "message": f"the database did not answer ({e.__class__.__name__}: {e})"}
        print(as_json(failed) if args.json else f"error: {failed['message']}", file=sys.stdout if args.json else sys.stderr)
        return 3
    print(as_json(result) if args.json else "", end="")
    if not args.json:
        print_result(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
