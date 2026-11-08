"""F3: months that depart most from the usual seasonal behaviour, per area.

1. Station-months (computed in MongoDB, one aggregation over the
   area/element/date index): usable values only (no quality flag, no P),
   with the number of days, the longest run of missing days, and the
   monthly value:
       TMAX, TMIN   monthly mean (degC)
       PRCP, SNOW   monthly total, scaled to the full month from the usable days (mm)
       SNWD         days with snow on the ground, scaled to the full month
   A station-month is valid under the WMO 3/5 rule: at most 5 missing days,
   at most 3 in a row. Invalid months are dropped, never filled.
2. Normals: for each station and calendar month, the mean and standard
   deviation over the valid reference months (default 1961-1990). A station
   gets a normal only with at least `min_ref_years` valid years (default 24
   of 30, the WMO 80 % rule).
3. Anomalies: value - normal, and the standardised anomaly z = (value -
   normal) / sd, which makes months and seasons comparable. For PRCP and
   SNOW, the percentage of normal is given too.
4. Area value, accounting for uneven station density: stations less than
   1 km apart count once; station anomalies are averaged within grid cells
   (default 1 degree), then cells are averaged with a cos(latitude) weight.
   A month is ranked only if at least `min_cells` (default 50 %) of the
   area's cells with a normal have data that month.
5. Months are ranked by |z| (or by z for --sign warm/wet, by -z for cold/dry).

Each ranked month keeps its evidence: the contributing stations with their
value, normal and anomaly, and the arguments to trace their daily records.
"""
import calendar
import collections
import datetime as dt
import math
import statistics

from . import AREAS
from .areas import distance_km

DAY_MS = 86_400_000
AMOUNTS = ("PRCP", "SNOW")


def _station_months(db, areas, element, y0, y1):
    match = {"area": {"$in": list(areas)}, "element": element,
             "date": {"$gte": dt.datetime(y0, 1, 1), "$lte": dt.datetime(y1, 12, 31)},
             "qflag": None}
    if element in AMOUNTS:
        match["mflag"] = {"$ne": "P"}
    pipeline = [
        {"$match": match},
        {"$project": {"station": 1, "date": 1, "value": 1,
                      "y": {"$year": "$date"}, "m": {"$month": "$date"}}},
        {"$setWindowFields": {"partitionBy": {"s": "$station", "y": "$y", "m": "$m"}, "sortBy": {"date": 1},
                              "output": {"prev": {"$shift": {"output": "$date", "by": -1}}}}},
        {"$group": {
            "_id": {"s": "$station", "y": "$y", "m": "$m"},
            "n": {"$sum": 1},
            "sum": {"$sum": "$value"},
            "pos": {"$sum": {"$cond": [{"$gt": ["$value", 0]}, 1, 0]}},
            "gap": {"$max": {"$cond": [{"$eq": ["$prev", None]}, 0,
                                       {"$subtract": [{"$divide": [{"$subtract": ["$date", "$prev"]}, DAY_MS]}, 1]}]}},
            "first": {"$min": {"$dayOfMonth": "$date"}},
            "last": {"$max": {"$dayOfMonth": "$date"}},
        }},
    ]
    out = {}
    for g in db.observations.aggregate(pipeline, allowDiskUse=True):
        s, y, m = g["_id"]["s"], g["_id"]["y"], g["_id"]["m"]
        dim = calendar.monthrange(y, m)[1]
        missing = dim - g["n"]
        longest = max(g["gap"], g["first"] - 1, dim - g["last"])
        if missing > 5 or longest > 3:
            continue
        if element in ("TMAX", "TMIN"):
            v = g["sum"] / g["n"]
        elif element in AMOUNTS:
            v = g["sum"] / g["n"] * dim
        else:  # SNWD: days with snow on the ground
            v = g["pos"] / g["n"] * dim
        out[(s, y, m)] = v
    return out


def _dedupe(stations: dict, ref_count: dict) -> tuple[set, list]:
    """Keep one station per site (< 1 km apart): the one with the most valid reference months."""
    ids = sorted(stations, key=lambda s: (-ref_count.get(s, 0), s))
    kept, dropped = [], []
    for s in ids:
        lon, lat = stations[s]["location"]["coordinates"]
        twin = next((k for k in kept if distance_km(lon, lat, *stations[k]["location"]["coordinates"]) < 1.0), None)
        if twin:
            dropped.append({"station": s, "kept": twin})
        else:
            kept.append(s)
    return set(kept), dropped


def monthly_anomalies(db, element, areas=AREAS, start_year=1991, end_year=2020, ref_start=1961, ref_end=1990,
                      months=None, grid=1.0, min_ref_years=24, min_cells=0.5, sign=None, top=10) -> dict:
    areas = list(areas)
    for a in areas:
        if a not in AREAS:
            raise ValueError(f"unknown area {a!r}")
    if sign not in (None, "high", "low"):
        raise ValueError("sign must be high or low")
    months = sorted(set(months)) if months else list(range(1, 13))

    y0, y1 = min(ref_start, start_year), max(ref_end, end_year)
    sm = _station_months(db, areas, element, y0, y1)
    stations = {s["_id"]: s for s in db.stations.find({"area": {"$in": areas}}, {"area": 1, "name": 1, "location": 1})}

    # normals per station and calendar month
    ref_vals = collections.defaultdict(list)
    for (s, y, m), v in sm.items():
        if ref_start <= y <= ref_end:
            ref_vals[(s, m)].append(v)
    normals = {k: (statistics.fmean(v), statistics.stdev(v)) for k, v in ref_vals.items() if len(v) >= min_ref_years}
    ref_count = collections.Counter(s for (s, _m) in normals)
    kept, dropped = _dedupe({s: stations[s] for s in ref_count if s in stations}, ref_count)

    def cell(s):
        lon, lat = stations[s]["location"]["coordinates"]
        return (math.floor(lat / grid), math.floor(lon / grid))

    def weight(c):
        return math.cos(math.radians((c[0] + 0.5) * grid))

    # cells that have at least one station with a normal, per area and calendar month
    eligible = collections.defaultdict(set)
    for (s, m) in normals:
        if s in kept:
            eligible[(stations[s]["area"], m)].add(cell(s))

    by_month = collections.defaultdict(list)  # (area, y, m) -> station anomalies
    for (s, y, m), v in sm.items():
        if not (start_year <= y <= end_year) or m not in months or s not in kept or (s, m) not in normals:
            continue
        mean, sd = normals[(s, m)]
        rec = {"station": s, "name": stations[s]["name"], "value": round(v, 2), "normal": round(mean, 2),
               "anomaly": round(v - mean, 2), "z": round((v - mean) / sd, 2) if sd > 0 else None}
        if element in AMOUNTS and mean > 0:
            rec["pct_of_normal"] = round(100 * v / mean, 1)
        by_month[(stations[s]["area"], y, m)].append(rec)

    results, excluded = [], []
    for (area, y, m), recs in by_month.items():
        cells = collections.defaultdict(list)
        for r in recs:
            cells[cell(r["station"])].append(r)
        n_elig = len(eligible[(area, m)])
        cover = len(cells) / n_elig if n_elig else 0
        if cover < min_cells:
            excluded.append({"area": area, "month": f"{y}-{m:02d}", "cells": len(cells), "eligible_cells": n_elig})
            continue

        def area_mean(key):
            num = den = 0.0
            for c, rs in cells.items():
                vals = [r[key] for r in rs if r.get(key) is not None]
                if vals:
                    num += weight(c) * statistics.fmean(vals)
                    den += weight(c)
            return round(num / den, 2) if den else None

        res = {"area": area, "month": f"{y}-{m:02d}", "normal": area_mean("normal"), "value": area_mean("value"),
               "anomaly": area_mean("anomaly"), "z": area_mean("z"),
               "stations": len(recs), "cells": len(cells), "eligible_cells": n_elig, "cell_coverage": round(cover, 2)}
        if element in AMOUNTS:
            res["pct_of_normal"] = area_mean("pct_of_normal")
        last = calendar.monthrange(y, m)[1]
        res["evidence"] = sorted(recs, key=lambda r: -abs(r["z"] or 0))
        res["trace"] = {"element": element, "from": f"{y}-{m:02d}-01", "to": f"{y}-{m:02d}-{last}"}
        results.append(res)

    key = {None: lambda r: -abs(r["z"] or 0), "high": lambda r: -(r["z"] or 0), "low": lambda r: (r["z"] or 0)}[sign]
    ranked = sorted(results, key=lambda r: (key(r), r["area"], r["month"]))
    per_area = {a: [r for r in ranked if r["area"] == a][:top] for a in areas}
    return {
        "service": "F3",
        "query": {"element": element, "areas": areas, "years": [start_year, end_year],
                  "reference": [ref_start, ref_end], "months": months, "grid_deg": grid,
                  "min_ref_years": min_ref_years, "min_cells": min_cells, "sign": sign},
        "reference_station_months": sum(1 for (_s, y, _m) in sm if ref_start <= y <= ref_end),
        "study_station_months": sum(1 for (_s, y, _m) in sm if start_year <= y <= end_year),
        "stations_with_normal": {a: len({s for s in kept if stations[s]["area"] == a}) for a in areas},
        "colocated_dropped": dropped,
        "months_ranked": len(results),
        "months_excluded_low_coverage": excluded,
        "ranking": ranked[:top * len(areas)],
        "per_area": per_area,
        "all_months": sorted(({k: v for k, v in r.items() if k not in ("evidence",)} for r in results),
                             key=lambda r: (r["area"], r["month"])),
        "complete": True,
    }
