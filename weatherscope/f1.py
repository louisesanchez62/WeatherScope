"""F1, second half: time-based summary of one station, with availability and quality.

For each element and each period (month, year or winter) the summary gives:
  * availability: expected days, days present, usable days (no quality flag),
    coverage = usable / expected, and whether the period is valid under the
    WMO 3/5 rule (every month in it: at most 5 missing days, at most 3 in a row);
  * quality: values with a quality flag, values flagged P (missing, presumed
    zero) and T (trace);
  * statistics on usable values only (P values are never used as zeros):
      TMAX, TMIN  mean, min, max (degC)
      PRCP, SNOW  total and days above zero (mm)
      SNWD        days with snow on the ground, max and mean depth (mm)

Everything is computed by one aggregation in MongoDB, at query time.
"""
import calendar
import datetime as dt

from . import ELEMENTS

# Sums are taken on the raw NOAA integers, which are exact whatever the order of
# addition (one server, several shards), and converted to degC / mm at the end.
SCALE = {"TMAX": 10, "TMIN": 10, "PRCP": 10, "SNOW": 1, "SNWD": 1}

PERIODS = ("month", "year", "winter")


def _period_expr(by: str) -> dict:
    if by == "month":
        return {"$dateToString": {"format": "%Y-%m", "date": "$date"}}
    if by == "year":
        return {"$dateToString": {"format": "%Y", "date": "$date"}}
    # winter: December of year Y + January and February of Y+1, labelled by Y
    return {"$cond": [{"$eq": [{"$month": "$date"}, 12]}, {"$year": "$date"},
                      {"$subtract": [{"$year": "$date"}, 1]}]}


def _period_months(by: str, key) -> list[tuple[int, int]]:
    if by == "month":
        y, m = map(int, key.split("-"))
        return [(y, m)]
    if by == "year":
        return [(int(key), m) for m in range(1, 13)]
    y = int(key)
    return [(y, 12), (y + 1, 1), (y + 1, 2)]


def _period_label(by: str, key) -> str:
    return f"{key}-{str(int(key) + 1)[-2:]}" if by == "winter" else str(key)


def _month_valid(present_days: set[int], y: int, m: int, start: dt.date, end: dt.date) -> bool | None:
    """WMO 3/5 rule on one month; None when the month is cut by the query range."""
    first, last = dt.date(y, m, 1), dt.date(y, m, calendar.monthrange(y, m)[1])
    if first < start or last > end:
        return None
    missing = run = longest = 0
    for d in range(1, last.day + 1):
        if d in present_days:
            run = 0
        else:
            missing += 1
            run += 1
            longest = max(longest, run)
    return missing <= 5 and longest <= 3


def station_summary(db, station: str, start: str, end: str, by: str = "month", elements=ELEMENTS) -> dict:
    if by not in PERIODS:
        raise ValueError(f"--by must be one of {', '.join(PERIODS)}")
    st = db.stations.find_one({"_id": station})
    if st is None:
        raise LookupError(f"station {station} is not in the database")
    d0, d1 = dt.date.fromisoformat(start), dt.date.fromisoformat(end)

    match = {"area": st["area"], "station": station, "element": {"$in": list(elements)},  # area: shard-key prefix (zoned)
             "date": {"$gte": dt.datetime.combine(d0, dt.time()), "$lte": dt.datetime.combine(d1, dt.time())}}
    usable = {"$eq": [{"$ifNull": ["$qflag", None]}, None]}
    amount_ok = {"$and": [usable, {"$ne": ["$mflag", "P"]}]}
    pipeline = [{"$match": match}]
    if by == "winter":
        pipeline.append({"$match": {"$expr": {"$in": [{"$month": "$date"}, [12, 1, 2]]}}})
    pipeline += [
        {"$group": {
            "_id": {"element": "$element", "period": _period_expr(by)},
            "present": {"$sum": 1},
            "usable": {"$sum": {"$cond": [usable, 1, 0]}},
            "qflagged": {"$sum": {"$cond": [usable, 0, 1]}},
            "mflag_P": {"$sum": {"$cond": [{"$eq": ["$mflag", "P"]}, 1, 0]}},
            "mflag_T": {"$sum": {"$cond": [{"$eq": ["$mflag", "T"]}, 1, 0]}},
            "n": {"$sum": {"$cond": [amount_ok, 1, 0]}},
            "sum_raw": {"$sum": {"$cond": [amount_ok, "$raw", 0]}},
            "min": {"$min": {"$cond": [amount_ok, "$value", None]}},
            "max": {"$max": {"$cond": [amount_ok, "$value", None]}},
            "positive": {"$sum": {"$cond": [{"$and": [amount_ok, {"$gt": ["$value", 0]}]}, 1, 0]}},
            "usable_dates": {"$push": {"$cond": [usable, "$date", "$$REMOVE"]}},
        }},
        {"$sort": {"_id.element": 1, "_id.period": 1}},
    ]

    rows = []
    for g in db.observations.aggregate(pipeline, allowDiskUse=True):
        el, key = g["_id"]["element"], g["_id"]["period"]
        months = _period_months(by, key)
        days_by_month = {}
        for d in g["usable_dates"]:
            days_by_month.setdefault((d.year, d.month), set()).add(d.day)
        expected = 0
        for y, m in months:
            first = max(dt.date(y, m, 1), d0)
            last = min(dt.date(y, m, calendar.monthrange(y, m)[1]), d1)
            expected += max((last - first).days + 1, 0)
        checks = [_month_valid(days_by_month.get((y, m), set()), y, m, d0, d1) for y, m in months]
        row = {
            "element": el, "period": _period_label(by, key),
            "expected_days": expected, "present": g["present"], "usable": g["usable"],
            "coverage": round(g["usable"] / expected, 3) if expected else None,
            "valid": None if any(c is None for c in checks) else all(checks),
            "qflagged": g["qflagged"], "mflag_P": g["mflag_P"], "mflag_T": g["mflag_T"],
        }
        n = g["n"]
        total = g["sum_raw"] / SCALE[el]
        if el in ("TMAX", "TMIN"):
            row.update(mean=round(total / n, 1) if n else None, min=g["min"], max=g["max"])
        elif el in ("PRCP", "SNOW"):
            row.update(total=round(total, 1) if n else None, days_positive=g["positive"])
        else:  # SNWD
            row.update(days_positive=g["positive"], max=g["max"], mean=round(total / n, 1) if n else None)
        rows.append(row)

    lon, lat = st["location"]["coordinates"]
    # Days the query asks for (only December-February in winter mode), with or without data.
    span = [d0 + dt.timedelta(days=i) for i in range((d1 - d0).days + 1)]
    asked = sum(1 for d in span if by != "winter" or d.month in (12, 1, 2))
    overall = {}
    for el in elements:
        r = [x for x in rows if x["element"] == el]
        if r:
            usable_days = sum(x["usable"] for x in r)
            overall[el] = {
                "usable_days": usable_days,
                "coverage": round(usable_days / asked, 3) if asked else None,
                "qflagged": sum(x["qflagged"] for x in r),
                "mflag_P": sum(x["mflag_P"] for x in r),
                "valid_periods": sum(1 for x in r if x["valid"]),
                "periods_with_data": len(r),
            }
    return {
        "service": "F1",
        "query": {"station": station, "start": start, "end": end, "by": by, "elements": list(elements)},
        "station": {"id": st["_id"], "name": st["name"], "area": st["area"], "region": st["region"],
                    "lat": lat, "lon": lon, "elev_m": st["elev_m"], "network": st["network"]},
        "overall": overall,
        "periods": rows,
        "complete": True,
    }
