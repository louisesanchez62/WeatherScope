"""F4: regional events versus isolated extremes.

An unusual station-day uses the same rule as F2 (fixed threshold or the
station's own 1961-1990 percentile), on usable values only.

Uneven station density is handled as in F3: stations less than 1 km apart
count once, stations are grouped in grid cells (default 1 degree) and cells
are weighted by cos(latitude). For every day:

  * reporting cells: cells with at least one station giving a usable value;
  * affected cells:  cells with at least one station meeting the rule on that
    day or within +/- `tolerance` days (default 1). The tolerance absorbs the
    observation-time shift: a station read at 8 a.m. books the previous
    afternoon's maximum on the next calendar day;
  * share = weight of affected cells / weight of reporting cells.

A missing station never counts as "not affected": it simply leaves the
denominator. Each day is then classified:
  * regional: share >= `min_share` (default 25 %) and at least `min_cells`
    affected cells (default 2);
  * local:    at least 2 affected cells, but below the regional share;
  * isolated: a single affected cell (with at least 3 cells reporting,
    otherwise "undetermined").
Consecutive regional days form a regional event; consecutive local/isolated
days form a local event (if any day has 2+ cells) or an isolated extreme.
Days touching a regional event belong to it. Severity = sum over the days of
share x mean excess beyond the threshold.
"""
import collections
import datetime as dt
import math
import statistics

from .f2 import select_days
from .f3 import _dedupe

ONE_DAY = dt.timedelta(days=1)


def regional_events(db, element, start, end, area=None, bbox=None, near=None, stations=None,
                    above=None, below=None, pct_above=None, pct_below=None,
                    tolerance=1, grid=1.0, min_share=0.25, min_cells=2, top=10, stable=False) -> dict:
    sel = select_days(db, element, start, end, area, bbox, near, stations, above, below, pct_above, pct_below,
                      stable)
    ids, days, d0, d1 = sel["ids"], sel["days"], sel["d0"], sel["d1"]
    direction, thresholds, fixed = sel["direction"], sel["thresholds"], sel["fixed"]

    st = {s["_id"]: s for s in db.stations.find({"_id": {"$in": ids}}, {"name": 1, "area": 1, "location": 1})}
    if thresholds is not None:  # percentile rule: only stations with a reference threshold take part
        st = {s: v for s, v in st.items() if s not in set(sel["skipped"])}

    # usable reporting stations per day (denominator)
    usable = {"qflag": None}
    if element in ("PRCP", "SNOW"):
        usable["mflag"] = {"$ne": "P"}
    reporting = {}
    for g in db.observations.aggregate([
        {"$match": {"area": {"$in": sorted({v["area"] for v in st.values()})}, "station": {"$in": sorted(st)},
                    "element": element, "date": {"$gte": d0, "$lte": d1}, **usable}},
        {"$group": {"_id": "$date", "s": {"$addToSet": "$station"}}},
    ], allowDiskUse=True):
        reporting[g["_id"]] = set(g["s"])

    activity = collections.Counter(s for v in reporting.values() for s in v)
    kept, merged = _dedupe(st, activity)

    def cell(s):
        lon, lat = st[s]["location"]["coordinates"]
        return (math.floor(lat / grid), math.floor(lon / grid))

    def weight(c):
        return math.cos(math.radians((c[0] + 0.5) * grid))

    def excess(s, day):
        v = days[s][day]
        t = thresholds[(s, day.month)] if thresholds is not None else fixed
        return (v - t) if direction == "above" else (t - v)

    # station-days meeting the rule, spread over +/- tolerance
    hits = collections.defaultdict(dict)  # day -> station -> (qualifying day, excess)
    for s, vals in days.items():
        if s not in kept:
            continue
        for day in vals:
            for k in range(-tolerance, tolerance + 1):
                d = day + k * ONE_DAY
                if d0 <= d <= d1:
                    prev = hits[d].get(s)
                    e = excess(s, day)
                    if prev is None or e > prev[1]:
                        hits[d][s] = (day, e)

    daily = {}
    for d in sorted(set(reporting) | set(hits)):
        rep = {cell(s) for s in reporting.get(d, ()) if s in kept}
        aff_st = hits.get(d, {})
        aff = {cell(s) for s in aff_st}
        denom = rep | aff
        w_den = sum(weight(c) for c in denom)
        share = sum(weight(c) for c in aff) / w_den if w_den else 0.0
        daily[d] = {"share": share, "cells": len(aff), "reporting_cells": len(denom),
                    "stations": aff_st, "mean_excess": statistics.fmean(e for _, e in aff_st.values()) if aff_st else 0}

    # classify each day, then group consecutive days of the same kind
    def kind(d):
        x = daily[d]
        if x["share"] >= min_share and x["cells"] >= min_cells:
            return "regional"
        if x["cells"] >= 2:
            return "local"
        if x["cells"] == 1:
            return "isolated"
        return None

    runs, run, run_kind = [], [], None
    for d in sorted(daily):
        k = kind(d)
        group = "regional" if k == "regional" else ("other" if k else None)
        if group and run and group == run_kind and d - run[-1] == ONE_DAY:
            run.append(d)
            continue
        if run:
            runs.append((run_kind, run))
        run, run_kind = ([d], group) if group else ([], None)
    if run:
        runs.append((run_kind, run))

    def touches_regional(r):
        return any(g == "regional" and (abs((x[0] - r[-1]).days) == 1 or abs((r[0] - x[-1]).days) == 1)
                   for g, x in runs)

    def build(r, scope):
        peak = max(r, key=lambda d: (daily[d]["share"], daily[d]["cells"]))
        involved = {}
        for d in r:
            for s_, (qday, e) in daily[d]["stations"].items():
                cur = involved.setdefault(s_, {"station": s_, "name": st[s_]["name"], "dates": set(), "max_excess": e})
                cur["dates"].add(qday)
                cur["max_excess"] = max(cur["max_excess"], e)
        evidence = sorted(({**v, "dates": sorted(x.date().isoformat() for x in v["dates"]),
                            "max_excess": round(v["max_excess"], 2)} for v in involved.values()),
                          key=lambda v: (-v["max_excess"], v["station"]))
        values = [days[s_][q] for s_, v in involved.items() for q in v["dates"]]
        return {
            "scope": scope,
            "start": r[0].date().isoformat(), "end": r[-1].date().isoformat(), "days": len(r),
            "peak_day": peak.date().isoformat(), "peak_share": round(daily[peak]["share"], 3),
            "peak_cells": daily[peak]["cells"], "reporting_cells": daily[peak]["reporting_cells"],
            "stations": len(involved),
            "severity": round(sum(daily[d]["share"] * daily[d]["mean_excess"] for d in r), 2),
            "extreme_value": (max(values) if direction == "above" else min(values)) if values else None,
            "evidence": evidence,
            "trace": {"element": element, "from": (r[0] - tolerance * ONE_DAY).date().isoformat(),
                      "to": (r[-1] + tolerance * ONE_DAY).date().isoformat(),
                      "stations": [e["station"] for e in evidence]},
        }

    out = []
    for group, r in runs:
        if group == "regional":
            out.append(build(r, "regional"))
        elif not touches_regional(r):  # days on the edge of a regional event belong to it, not to a new event
            ev = build(r, "local" if any(kind(d) == "local" for d in r) else "isolated")
            if ev["scope"] == "isolated" and ev["reporting_cells"] < 3:
                ev["scope"] = "undetermined"  # too few cells reporting to call it isolated
            out.append(ev)

    by_scope = collections.Counter(e["scope"] for e in out)
    ranking = {sc: [e["start"] for e in sorted((e for e in out if e["scope"] == sc),
                                               key=lambda e: (-e["severity"], e["start"]))][:top]
               for sc in ("regional", "local", "isolated")}
    return {
        "service": "F4",
        "query": {"element": element, "start": start, "end": end, "area": area, "bbox": bbox, "near": near,
                  "stations": stations, "rule": {"above": above, "below": below, "pct_above": pct_above,
                                                 "pct_below": pct_below},
                  "tolerance_days": tolerance, "grid_deg": grid, "min_share": min_share, "min_cells": min_cells,
                  "stable_network": stable},
        "stations_used": len(kept), "colocated_merged": merged,
        "stations_without_reference": sel["skipped"],
        "days_with_data": len(reporting),
        "count": len(out),
        "count_by_scope": dict(by_scope),
        "events": sorted(out, key=lambda e: (e["start"], e["scope"])),  # canonical order
        "ranking": ranking,
        "complete": True,
    }
