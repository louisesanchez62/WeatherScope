"""Monthly read model: one document per station, element and month.

    {_id: "USW00094847:SNWD:2014-01", station, area, element, year, month,
     n: 31, days: [{d: 1, raw, v, m, q, s, t}, ...]}           (days sorted)

Hybrid design. `observations` (one document per observation) stays the
source of truth: F5 writes there, history and trace point there, the
validator checks every value. `obs_months` is derived from it and serves the
regional analyses, which read whole months: F3 is about 13 times faster on it
(Part I measurements, results/part1/compare_designs.json).

  * build()   rebuilds the whole collection from `observations`;
  * refresh() rebuilds only the months an import touched (called by F5),
              so the read model always matches the source of truth;
  * station_months() is F3's monthly table read from buckets;
  * f1_months() answers F1 by month (used in the Part I comparison).
"""
import calendar
import collections
import datetime as dt

from .f1 import SCALE

COLL = "obs_months"
AMOUNTS = ("PRCP", "SNOW")


def _ensure_indexes(db):
    db[COLL].create_index([("station", 1), ("element", 1), ("year", 1), ("month", 1)], name="station_element_ym")
    db[COLL].create_index([("area", 1), ("element", 1), ("year", 1), ("month", 1)], name="area_element_ym")


def _is_cluster(db) -> bool:
    return db.client.admin.command("hello").get("msg") == "isdbgrid"


def available(db, areas=None) -> bool:
    """Is the read model built? With `areas`, only those areas are looked at, so on a zoned
    cluster the check reaches only their shards (a whole-collection count would contact every
    shard, including a stopped one the request does not need)."""
    if areas:
        return db[COLL].find_one({"area": {"$in": list(areas)}}, {"_id": 1}) is not None
    return db[COLL].estimated_document_count() > 0


def refresh(db, keys) -> int:
    """Rebuild the buckets of the given (station, element, year, month) keys from `observations`.

    Months are grouped per station and element; each group is one aggregation over
    the station/element/date index, so an import of a few years for many stations
    stays cheap. Returns the number of months rebuilt."""
    if not keys:
        return 0
    _ensure_indexes(db)
    by_se = collections.defaultdict(set)
    for s, e, y, m in keys:
        by_se[(s, e)].add((y, m))
    for (s, e), months in by_se.items():
        (y0, m0), (y1, m1) = min(months), max(months)
        last = calendar.monthrange(y1, m1)[1]
        _aggregate(db, {"station": s, "element": e,
                        "date": {"$gte": dt.datetime(y0, m0, 1), "$lte": dt.datetime(y1, m1, last)}})
    return sum(len(v) for v in by_se.values())


def build(db, log=print) -> None:
    sharded = db.client.config.collections.find_one({"_id": f"{db.name}.{COLL}"}) if _is_cluster(db) else None
    if sharded:
        db[COLL].delete_many({})  # keep the collection and its sharding (dropping would unshard it)
    else:
        db[COLL].drop()
    _aggregate(db, None)
    _ensure_indexes(db)
    log(f"{COLL}: {db[COLL].estimated_document_count():,} documents")


def _aggregate(db, match):
    """Group observations into month buckets and merge them into `obs_months` (replacing existing ones)."""
    db.observations.aggregate(([{"$match": match}] if match else []) + [
        {"$group": {
            "_id": {"s": "$station", "e": "$element", "y": {"$year": "$date"}, "m": {"$month": "$date"}},
            "area": {"$first": "$area"},
            "days": {"$push": {"d": {"$dayOfMonth": "$date"}, "raw": "$raw", "v": "$value", "m": "$mflag",
                               "q": "$qflag", "s": "$sflag", "t": "$obs_time"}},
        }},
        {"$project": {
            "_id": {"$concat": ["$_id.s", ":", "$_id.e", ":", {"$toString": "$_id.y"}, "-",
                                {"$cond": [{"$lt": ["$_id.m", 10]}, "0", ""]}, {"$toString": "$_id.m"}]},
            "station": "$_id.s", "area": 1, "element": "$_id.e", "year": "$_id.y", "month": "$_id.m",
            "n": {"$size": "$days"},
            "days": {"$sortArray": {"input": "$days", "sortBy": {"d": 1}}},
        }},
        {"$merge": {"into": COLL, "whenMatched": "replace"}},
    ], allowDiskUse=True)


def _usable_filter(element):
    cond = {"$eq": [{"$ifNull": ["$$x.q", None]}, None]}
    if element in AMOUNTS:
        cond = {"$and": [cond, {"$ne": ["$$x.m", "P"]}]}
    return {"$filter": {"input": "$days", "as": "x", "cond": cond}}


def station_months(db, areas, element, y0, y1) -> dict:
    """Same output as weatherscope.f3._station_months, from the bucket model."""
    rows = db[COLL].aggregate([
        {"$match": {"area": {"$in": list(areas)}, "element": element, "year": {"$gte": y0, "$lte": y1}}},
        {"$project": {"_id": 0, "station": 1, "year": 1, "month": 1, "u": _usable_filter(element)}},
        {"$project": {"station": 1, "year": 1, "month": 1, "ds": "$u.d",
                      "sum_raw": {"$sum": "$u.raw"},
                      "pos": {"$size": {"$filter": {"input": "$u", "as": "x", "cond": {"$gt": ["$$x.v", 0]}}}}}},
    ], allowDiskUse=True)
    out = {}
    for r in rows:
        ds = r["ds"]
        if not ds:
            continue
        y, m = r["year"], r["month"]
        dim = calendar.monthrange(y, m)[1]
        missing = dim - len(ds)
        gaps = [b - a - 1 for a, b in zip(ds, ds[1:])]
        longest = max([ds[0] - 1, dim - ds[-1]] + gaps)
        if missing > 5 or longest > 3:
            continue
        n = len(ds)
        if element in ("TMAX", "TMIN"):
            v = r["sum_raw"] / SCALE[element] / n
        elif element in AMOUNTS:
            v = r["sum_raw"] / SCALE[element] / n * dim
        else:
            v = r["pos"] / n * dim
        out[(r["station"], y, m)] = v
    return out


def f1_months(db, station, y0, y1, elements) -> list[dict]:
    """F1 by month (availability, quality, statistics) from the bucket model.
    Covers whole calendar years y0..y1; returns rows comparable to f1.station_summary(by="month")."""
    rows = []
    for el in elements:
        for b in db[COLL].aggregate([
            {"$match": {"station": station, "element": el, "year": {"$gte": y0, "$lte": y1}}},
            {"$project": {"_id": 0, "year": 1, "month": 1, "n": 1, "days": 1, "u": _usable_filter(el)}},
            {"$sort": {"year": 1, "month": 1}},
        ]):
            y, m = b["year"], b["month"]
            dim = calendar.monthrange(y, m)[1]
            u = b["u"]
            vals = [x["v"] for x in u]
            usable_days = {x["d"] for x in b["days"] if x.get("q") is None}
            missing = run = longest = 0
            for d in range(1, dim + 1):
                if d in usable_days:
                    run = 0
                else:
                    missing += 1
                    run += 1
                    longest = max(longest, run)
            row = {"element": el, "period": f"{y}-{m:02d}", "expected_days": dim, "present": b["n"],
                   "usable": len(usable_days), "coverage": round(len(usable_days) / dim, 3),
                   "valid": missing <= 5 and longest <= 3,
                   "qflagged": sum(1 for x in b["days"] if x.get("q") is not None),
                   "mflag_P": sum(1 for x in b["days"] if x.get("m") == "P"),
                   "mflag_T": sum(1 for x in b["days"] if x.get("m") == "T")}
            n = len(vals)
            total = sum(x["raw"] for x in u) / SCALE[el]
            if el in ("TMAX", "TMIN"):
                row.update(mean=round(total / n, 1) if n else None, min=min(vals) if n else None,
                           max=max(vals) if n else None)
            elif el in AMOUNTS:
                row.update(total=round(total, 1) if n else None, days_positive=sum(1 for v in vals if v > 0))
            else:
                row.update(days_positive=sum(1 for v in vals if v > 0), max=max(vals) if n else None,
                           mean=round(total / n, 1) if n else None)
            rows.append(row)
    return rows
