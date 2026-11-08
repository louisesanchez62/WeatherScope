#!/usr/bin/env python3
"""Load GHCN-Daily CSV files into MongoDB as one import batch.

The same code serves the initial load and F5 updates. Each run is a batch
(batches._id = <YYYY-MM-DD>-<label>). For every row of TMAX, TMIN, PRCP, SNOW
or SNWD, from a selected station and inside --start/--end:

  * new _id                                  -> insert          (inserted)
  * same _id, same raw value and flags        -> leave untouched (unchanged)
  * same _id, different raw value or a flag   -> copy the stored version to
                                                 history, then replace (revised)
  * row that cannot be parsed, or a second row
    for the same _id in the same batch        -> skip            (rejected)

Loading the same files twice therefore gives inserted = revised = 0 the second
time. Accepts the per-station files (by_station/<ID>.csv.gz) and the per-year
files (by_year/<YYYY>.csv.gz), which share the same columns.

Also upserts the selected stations into `stations`, and writes a timing and
count summary to results/ingest/<batch>.json (raw measurements for E1).

Usage:
    python3 scripts/load_observations.py --label initial
    python3 scripts/load_observations.py --label f5-2021 --files path/to/2021.csv.gz --end 2021-12-31
"""
import argparse
import collections
import csv
import datetime as dt
import gzip
import hashlib
import io
import json
import os
import pathlib
import re
import time

from pymongo import InsertOne, MongoClient, ReplaceOne

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCOPE = ROOT / "data" / "scope" / "stations.csv"
BY_STATION = ROOT / "data" / "raw" / "by_station"
RESULTS = ROOT / "results" / "ingest"

SCALE = {"TMAX": 10, "TMIN": 10, "PRCP": 10, "SNOW": 1, "SNWD": 1}
ELEMENTS = tuple(SCALE)
COMPARED = ("raw", "mflag", "qflag", "sflag", "obs_time")
STUDY = (dt.datetime(1991, 1, 1), dt.datetime(2020, 12, 31))
REFERENCE = (dt.datetime(1961, 1, 1), dt.datetime(1990, 12, 31))
CHUNK = 5000


# ---------------------------------------------------------------- stations

def read_scope() -> dict[str, dict]:
    with SCOPE.open() as f:
        return {r["id"]: r for r in csv.DictReader(f) if r["selected"] == "True"}


def station_doc(r: dict) -> dict:
    elev = float(r["elev_m"])
    return {
        "_id": r["id"],
        "name": r["name"],
        "area": r["area"],
        "region": r["region"],
        "location": {"type": "Point", "coordinates": [float(r["lon"]), float(r["lat"])]},
        "elev_m": None if elev <= -999 else elev,
        "network": r["network"],
        "long_record": r["long_record"] == "True",
        "reference_record": r["reference_record"] == "True",
        "elements": {e: {"first": int(r[f"{e}_first"]), "last": int(r[f"{e}_last"])}
                     for e in ELEMENTS if r[f"{e}_first"]},
    }


def upsert_stations(db, scope: dict, ids) -> int:
    ops = [ReplaceOne({"_id": sid}, station_doc(scope[sid]), upsert=True) for sid in sorted(ids)]
    if ops:
        db.stations.bulk_write(ops, ordered=False)
    return len(ops)


# ---------------------------------------------------------------- parsing

def sha256(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def open_text(path: pathlib.Path):
    raw = path.open("rb")
    if raw.read(2) == b"\x1f\x8b":
        raw.seek(0)
        return io.TextIOWrapper(gzip.GzipFile(fileobj=raw), newline="")
    raw.seek(0)
    return io.TextIOWrapper(raw, newline="")


def flag(s: str):
    return s if s else None


class Stats:
    def __init__(self):
        self.counts = collections.Counter()   # inserted / unchanged / revised / rejected
        self.reasons = collections.Counter()  # why rows were rejected or skipped
        self.periods = collections.Counter()  # rows in scope by period
        self.stations = set()
        self.file_rows = 0                    # rows in scope in the current file


def parse_rows(path, scope, start, end, batch_id, stats, seen):
    """Yield observation documents from one file; count everything else."""
    stats.file_rows = 0
    with open_text(path) as f:
        for rec in csv.reader(f):
            if len(rec) < 8:
                if rec and rec[0].upper() != "ID":  # tolerate a header line
                    stats.counts["rejected"] += 1
                    stats.reasons["row with fewer than 8 columns"] += 1
                continue
            sid, day, element, value, mflag, qflag, sflag, obs_time = rec[:8]
            if element not in SCALE:
                continue
            if sid not in scope:
                stats.reasons["station outside our scope (skipped)"] += 1
                continue
            try:
                date = dt.datetime.strptime(day, "%Y%m%d")
            except ValueError:
                stats.counts["rejected"] += 1
                stats.reasons["unreadable date"] += 1
                continue
            if not (start <= date <= end):
                continue
            stats.file_rows += 1
            try:
                raw = int(value)
            except ValueError:
                stats.counts["rejected"] += 1
                stats.reasons["unreadable value"] += 1
                continue
            if any(len(x) > 1 for x in (mflag, qflag, sflag)) or (
                    obs_time and not (len(obs_time) == 4 and obs_time.isdigit())):
                stats.counts["rejected"] += 1
                stats.reasons["malformed flag or observation time"] += 1
                continue
            oid = f"{sid}:{date:%Y-%m-%d}:{element}"
            if oid in seen:
                stats.counts["rejected"] += 1
                stats.reasons["second row for the same station, date and element"] += 1
                continue
            seen.add(oid)
            if STUDY[0] <= date <= STUDY[1]:
                stats.periods["study_1991_2020"] += 1
            elif REFERENCE[0] <= date <= REFERENCE[1]:
                stats.periods["reference_1961_1990"] += 1
            else:
                stats.periods["other"] += 1
            stats.stations.add(sid)
            yield {
                "_id": oid,
                "station": sid,
                "area": scope[sid]["area"],
                "date": date,
                "element": element,
                "raw": raw,
                "value": float(raw) / SCALE[element],
                "mflag": flag(mflag),
                "qflag": flag(qflag),
                "sflag": flag(sflag),
                "obs_time": obs_time or None,
                "source": {"file": path.name, "batch": batch_id},
            }


# ---------------------------------------------------------------- writing

def flush(db, docs, batch_id, stats):
    if not docs:
        return
    stations = sorted({d["station"] for d in docs})
    ids = [d["_id"] for d in docs]
    existing = {e["_id"]: e for e in db.observations.find(
        {"station": {"$in": stations}, "_id": {"$in": ids}},
        {k: 1 for k in COMPARED + ("value", "source")})}
    now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    writes, history = [], []
    for d in docs:
        old = existing.get(d["_id"])
        if old is None:
            writes.append(InsertOne(d))
            stats.counts["inserted"] += 1
        elif all(old.get(k) == d[k] for k in COMPARED):
            stats.counts["unchanged"] += 1
        else:
            history.append({
                "obs_id": d["_id"], "replaced_at": now, "by_batch": batch_id,
                "previous": {k: old.get(k) for k in COMPARED + ("value", "source")},
            })
            writes.append(ReplaceOne({"_id": d["_id"], "station": d["station"]}, d))
            stats.counts["revised"] += 1
    if history:
        db.history.insert_many(history, ordered=False)
    if writes:
        db.observations.bulk_write(writes, ordered=False)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--uri", default=os.environ.get("WEATHERSCOPE_URI", "mongodb://localhost:27017"))
    ap.add_argument("--db", default=os.environ.get("WEATHERSCOPE_DB", "weatherscope"))
    ap.add_argument("--label", required=True, help="batch label, e.g. initial or f5-2021")
    ap.add_argument("--files", nargs="+", type=pathlib.Path,
                    help="GHCN CSV files to load (default: downloaded by_station files of the selected stations)")
    ap.add_argument("--stations", nargs="+", metavar="ID", help="only these stations")
    ap.add_argument("--long-only", action="store_true", help="only stations flagged long_record")
    ap.add_argument("--start", default="1961-01-01", help="first date loaded (default 1961-01-01)")
    ap.add_argument("--end", default="2020-12-31", help="last date loaded (default 2020-12-31)")
    args = ap.parse_args()

    start = dt.datetime.fromisoformat(args.start)
    end = dt.datetime.fromisoformat(args.end)
    scope = read_scope()
    if args.stations:
        scope = {k: v for k, v in scope.items() if k in set(args.stations)}
    if args.long_only:
        scope = {k: v for k, v in scope.items() if v["long_record"] == "True"}

    if args.files:
        files = args.files
    else:
        files = [BY_STATION / f"{sid}.csv.gz" for sid in sorted(scope)]
        missing = [p for p in files if not p.exists()]
        files = [p for p in files if p.exists()]
        if missing:
            print(f"warning: {len(missing)} selected stations have no downloaded file "
                  f"(run fetch_observations.py), e.g. {missing[0].name}")
    if not files:
        raise SystemExit("no files to load")

    if not re.fullmatch(r"[a-z0-9-]+", args.label):
        raise SystemExit("--label may only contain lowercase letters, digits and dashes")
    batch_id = f"{dt.date.today():%Y-%m-%d}-{args.label}"
    db = MongoClient(args.uri, serverSelectionTimeoutMS=5000)[args.db]
    if db.batches.count_documents({"_id": batch_id}):
        raise SystemExit(f"batch {batch_id} already exists: choose another --label")

    t0 = time.perf_counter()
    started = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    db.batches.insert_one({"_id": batch_id, "status": "running", "started": started, "finished": None,
                           "files": [], "counts": {k: 0 for k in ("inserted", "unchanged", "revised", "rejected")}})

    stats = Stats()
    n_stations = 0
    file_entries = []
    seen = set()
    status = "failed"
    try:
        n_stations = upsert_stations(db, scope, scope.keys() if not args.files else [])
        for i, path in enumerate(files, 1):
            buffer = []
            for doc in parse_rows(path, scope, start, end, batch_id, stats, seen):
                buffer.append(doc)
                if len(buffer) >= CHUNK:
                    flush(db, buffer, batch_id, stats)
                    buffer = []
            flush(db, buffer, batch_id, stats)
            file_entries.append({"name": path.name, "sha256": sha256(path),
                                 "rows": stats.file_rows})
            if i % 50 == 0 or i == len(files):
                c = stats.counts
                print(f"  {i}/{len(files)} files  inserted {c['inserted']:,}  unchanged {c['unchanged']:,}  "
                      f"revised {c['revised']:,}  rejected {c['rejected']:,}  {time.perf_counter() - t0:.0f}s")
        if args.files:  # stations met in an external extract
            n_stations = upsert_stations(db, scope, stats.stations)
        status = "done"
    finally:
        elapsed = time.perf_counter() - t0
        counts = {k: int(stats.counts[k]) for k in ("inserted", "unchanged", "revised", "rejected")}
        db.batches.update_one({"_id": batch_id}, {"$set": {
            "status": status, "finished": dt.datetime.now(dt.timezone.utc).replace(tzinfo=None),
            "files": file_entries, "counts": counts}})
        summary = {
            "batch": batch_id, "status": status, "uri": args.uri, "db": args.db,
            "start": args.start, "end": args.end, "files": len(file_entries),
            "stations_with_rows": len(stats.stations), "elapsed_s": round(elapsed, 2),
            "counts": counts, "rows_by_period": dict(stats.periods),
            "notes": dict(stats.reasons),
        }
        RESULTS.mkdir(parents=True, exist_ok=True)
        (RESULTS / f"{batch_id}.json").write_text(json.dumps(summary, indent=2) + "\n")
        print(json.dumps(summary, indent=2))
        print(f"stations upserted: {n_stations if status == 'done' else 'n/a'}")


if __name__ == "__main__":
    main()
