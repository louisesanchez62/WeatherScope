"""F5: import a batch of genuine NOAA observations (initial load or update).

Identity of an observation: station + date + element (its _id). For every row
of TMAX, TMIN, PRCP, SNOW or SNWD, from a station in scope and inside the
date range:

  * new _id                                  -> insert          (inserted)
  * same _id, same raw value and flags        -> leave untouched (unchanged)
  * same _id, different raw value or a flag   -> copy the stored version to
                                                 history, then replace (revised)
  * row that cannot be parsed, or a second row
    for the same _id in the same batch        -> skip            (rejected)

Policy for revised records: keep a history. NOAA revises its archive
continuously; the newest value is what the services use, and the previous one
stays traceable (trace --history). Importing the same files twice gives
inserted = revised = 0: the observations and the history are unchanged, only
the import log (batches) records the second attempt.

After writing, the months that received an inserted or revised observation are
rebuilt in the monthly read model (obs_months), so F3 sees the import at once.

Accepts per-station (by_station/<ID>.csv.gz) and per-year (by_year/<YYYY>.csv.gz)
files, gzipped or not. Rows from stations outside our two areas are skipped.
"""
import collections
import csv
import datetime as dt
import gzip
import hashlib
import io
import json
import pathlib
import re
import time

from pymongo import InsertOne, ReplaceOne

from . import bucket

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

def read_scope(include_unselected: bool = False) -> dict[str, dict]:
    """Stations of our two areas from data/scope/stations.csv. By default only the
    selected ones; with include_unselected, also those left out of the initial
    load (above 800 m, or without data in 1991-2020)."""
    with SCOPE.open() as f:
        return {r["id"]: r for r in csv.DictReader(f) if include_unselected or r["selected"] == "True"}


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
        self.touched = set()                  # (station, element, year, month) written, for the read model


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
            stats.touched.add((d["station"], d["element"], d["date"].year, d["date"].month))
        elif all(old.get(k) == d[k] for k in COMPARED):
            stats.counts["unchanged"] += 1
        else:
            history.append({
                "obs_id": d["_id"], "replaced_at": now, "by_batch": batch_id,
                "previous": {k: old.get(k) for k in COMPARED + ("value", "source")},
            })
            writes.append(ReplaceOne({"_id": d["_id"], "station": d["station"]}, d))
            stats.counts["revised"] += 1
            stats.touched.add((d["station"], d["element"], d["date"].year, d["date"].month))
    if history:
        db.history.insert_many(history, ordered=False)
    if writes:
        db.observations.bulk_write(writes, ordered=False)


def import_batch(db, label, files=None, stations=None, long_only=False, start="1961-01-01", end="2020-12-31",
                 include_unselected=False, log=print) -> dict:
    """Run one import batch and return its summary (also written to results/ingest/<batch>.json)."""
    if not re.fullmatch(r"[a-z0-9-]+", label):
        raise ValueError("label may only contain lowercase letters, digits and dashes")
    d0, d1 = dt.datetime.fromisoformat(start), dt.datetime.fromisoformat(end)
    scope = read_scope(include_unselected)
    if stations:
        unknown = set(stations) - set(scope)
        if unknown:
            raise ValueError(f"not in our areas{'' if include_unselected else ' or not selected'}: "
                             f"{', '.join(sorted(unknown))}")
        scope = {k: v for k, v in scope.items() if k in set(stations)}
    if long_only:
        scope = {k: v for k, v in scope.items() if v["long_record"] == "True"}

    if files:
        files = [pathlib.Path(f) for f in files]
        missing = [str(p) for p in files if not p.exists()]
        if missing:
            raise ValueError(f"file not found: {', '.join(missing)}")
    else:
        files = [BY_STATION / f"{sid}.csv.gz" for sid in sorted(scope)]
        missing = [p for p in files if not p.exists()]
        files = [p for p in files if p.exists()]
        if missing:
            log(f"warning: {len(missing)} stations have no downloaded file (run fetch_observations.py), "
                f"e.g. {missing[0].name}")
    if not files:
        raise ValueError("no files to load")

    batch_id = f"{dt.date.today():%Y-%m-%d}-{label}"
    if db.batches.count_documents({"_id": batch_id}):
        raise ValueError(f"batch {batch_id} already exists: choose another label")

    t0 = time.perf_counter()
    started = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    db.batches.insert_one({"_id": batch_id, "status": "running", "started": started, "finished": None,
                           "files": [], "counts": {k: 0 for k in ("inserted", "unchanged", "revised", "rejected")}})
    stats = Stats()
    months_refreshed, refresh_s = None, None
    file_entries, seen = [], set()
    status = "failed"
    try:
        for i, path in enumerate(files, 1):
            buffer = []
            for doc in parse_rows(path, scope, d0, d1, batch_id, stats, seen):
                buffer.append(doc)
                if len(buffer) >= CHUNK:
                    flush(db, buffer, batch_id, stats)
                    buffer = []
            flush(db, buffer, batch_id, stats)
            file_entries.append({"name": path.name, "sha256": sha256(path), "rows": stats.file_rows})
            if i % 50 == 0 or i == len(files):
                c = stats.counts
                log(f"  {i}/{len(files)} files  inserted {c['inserted']:,}  unchanged {c['unchanged']:,}  "
                    f"revised {c['revised']:,}  rejected {c['rejected']:,}  {time.perf_counter() - t0:.0f}s")
        n_stations = upsert_stations(db, scope, stats.stations)  # stations that actually brought rows
        # keep the monthly read model in step with the source of truth
        t_refresh = time.perf_counter()
        if bucket.available(db):
            months_refreshed = bucket.refresh(db, stats.touched)
        else:
            months_refreshed = None  # no read model yet: build it with weatherscope.bucket.build
        refresh_s = round(time.perf_counter() - t_refresh, 2)
        status = "done"
    finally:
        elapsed = time.perf_counter() - t0
        counts = {k: int(stats.counts[k]) for k in ("inserted", "unchanged", "revised", "rejected")}
        db.batches.update_one({"_id": batch_id}, {"$set": {
            "status": status, "finished": dt.datetime.now(dt.timezone.utc).replace(tzinfo=None),
            "files": file_entries, "counts": counts}})
    summary = {
        "service": "F5", "batch": batch_id, "status": status, "db": db.name,
        "start": start, "end": end, "files": len(file_entries),
        "stations_with_rows": len(stats.stations), "stations_upserted": n_stations,
        "elapsed_s": round(elapsed, 2), "counts": counts,
        "read_model_months_refreshed": months_refreshed, "read_model_refresh_s": refresh_s, "rows_by_period": dict(stats.periods),
        "notes": dict(stats.reasons), "complete": True,
    }
    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / f"{batch_id}.json").write_text(json.dumps(summary, indent=2) + "\n")
    return summary
