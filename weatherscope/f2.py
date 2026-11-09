"""F2: prolonged events, i.e. runs of consecutive days meeting a user rule.

Rule, on one element:
  * fixed threshold:   value >= X  (--above)  or  value <= X  (--below)
  * station percentile: value above (or below) the station's own percentile
    for that calendar month over the 1961-1990 reference. For PRCP and SNOW
    the percentile is taken over days with at least 1 mm, as in the usual
    climate indices; stations with too few reference values get no
    threshold and are listed as skipped.

A day counts only if its value is usable: no quality flag, and never a P
(missing, presumed zero) value. Missing and flagged days therefore always
break an episode; they can neither create nor extend one. Episodes are built
on the continuous date sequence, so a run from December into January is a
single episode.

Only the qualifying days are read from MongoDB (through the station/element/
date index); the days just before and after each episode are then looked up
by _id to say why it started and ended (rule not met, missing, flagged, or
edge of the query).
"""
import collections
import datetime as dt

from .areas import station_filter

AMOUNTS = ("PRCP", "SNOW")
MIN_REF_VALUES = 60
REFERENCE = (dt.datetime(1961, 1, 1), dt.datetime(1990, 12, 31))
ONE_DAY = dt.timedelta(days=1)


def _usable(element: str) -> dict:
    q = {"qflag": None}
    if element in AMOUNTS:
        q["mflag"] = {"$ne": "P"}
    return q


def _thresholds(db, stations, element, pct, direction):
    """Per station and calendar month: the reference percentile and how many values it rests on."""
    match = {"station": {"$in": stations}, "element": element,
             "date": {"$gte": REFERENCE[0], "$lte": REFERENCE[1]}, **_usable(element)}
    if element in AMOUNTS:
        match["value"] = {"$gte": 1.0}
    p = pct / 100 if direction == "above" else 1 - pct / 100
    out = {}
    for g in db.observations.aggregate([
        {"$match": match},
        {"$group": {"_id": {"s": "$station", "m": {"$month": "$date"}}, "n": {"$sum": 1},
                    "p": {"$percentile": {"input": "$value", "p": [p], "method": "approximate"}}}},
    ], allowDiskUse=True):
        if g["n"] >= MIN_REF_VALUES:
            out[(g["_id"]["s"], g["_id"]["m"])] = g["p"][0]
    return out


def select_days(db, element, start, end, area=None, bbox=None, near=None, stations=None,
                above=None, below=None, pct_above=None, pct_below=None) -> dict:
    """Usable days meeting the rule, per station. Shared by F2 and F4."""
    rules = [r for r in (above, below, pct_above, pct_below) if r is not None]
    if len(rules) != 1:
        raise ValueError("give exactly one rule: --above, --below, --pct-above or --pct-below")
    direction = "above" if (above is not None or pct_above is not None) else "below"
    percentile = pct_above if pct_above is not None else pct_below
    if percentile is not None and not 50 <= percentile < 100:
        raise ValueError("percentile must be between 50 and 100")

    sq = station_filter(area, bbox, near)
    if stations:
        sq["_id"] = {"$in": list(stations)}
    if not sq:
        raise ValueError("give an area (--area, --bbox, --near) or --station")
    meta = {s["_id"]: s for s in db.stations.find(sq, {"name": 1, "area": 1})}
    ids = sorted(meta)
    d0, d1 = dt.datetime.fromisoformat(start), dt.datetime.fromisoformat(end)

    areas = sorted({m["area"] for m in meta.values()})  # shard-key prefix on the zoned cluster
    q = {"area": {"$in": areas}, "station": {"$in": ids}, "element": element,
         "date": {"$gte": d0, "$lte": d1}, **_usable(element)}
    thresholds, skipped = None, []
    if percentile is None:
        fixed = above if direction == "above" else below
        q["value"] = {"$gte": fixed} if direction == "above" else {"$lte": fixed}
    else:
        thresholds = _thresholds(db, ids, element, percentile, direction)
        have = {s for s, _ in thresholds}
        skipped = [s for s in ids if s not in have]

    # 1. qualifying days per station
    days = collections.defaultdict(dict)
    for d in db.observations.find(q, {"_id": 0, "station": 1, "date": 1, "value": 1}).hint("station_element_date"):
        if thresholds is not None:
            t = thresholds.get((d["station"], d["date"].month))
            if t is None or (d["value"] <= t if direction == "above" else d["value"] >= t):
                continue
        days[d["station"]][d["date"]] = d["value"]

    return {"meta": meta, "ids": ids, "d0": d0, "d1": d1, "direction": direction,
            "thresholds": thresholds, "skipped": skipped, "days": days,
            "fixed": None if percentile is not None else (above if direction == "above" else below)}


def find_episodes(db, element, start, end, area=None, bbox=None, near=None, stations=None,
                  above=None, below=None, pct_above=None, pct_below=None, min_days=1) -> dict:
    sel = select_days(db, element, start, end, area, bbox, near, stations, above, below, pct_above, pct_below)
    meta, ids, d0, d1 = sel["meta"], sel["ids"], sel["d0"], sel["d1"]
    direction, thresholds, skipped, days = sel["direction"], sel["thresholds"], sel["skipped"], sel["days"]

    # 2. join consecutive days into episodes
    episodes = []
    for sid, vals in days.items():
        run = []
        for day in sorted(vals):
            if run and day - run[-1] != ONE_DAY:
                episodes.append((sid, run))
                run = []
            run.append(day)
        if run:
            episodes.append((sid, run))
    episodes = [(s, r) for s, r in episodes if len(r) >= min_days]

    # 3. why each episode starts and ends: look up the neighbouring days by _id
    def oid(s, day):
        return f"{s}:{day:%Y-%m-%d}:{element}"
    edges = {}
    for s, r in episodes:
        for day in (r[0] - ONE_DAY, r[-1] + ONE_DAY):
            if d0 <= day <= d1:
                edges[oid(s, day)] = None
    if edges:
        for doc in db.observations.find({"station": {"$in": sorted({k.split(":")[0] for k in edges})},
                                         "_id": {"$in": list(edges)}},
                                        {"value": 1, "qflag": 1, "mflag": 1}):
            edges[doc["_id"]] = doc

    def reason(s, day):
        if not d0 <= day <= d1:
            return "query limit"
        doc = edges.get(oid(s, day))
        if doc is None:
            return "missing day"
        if doc.get("qflag") is not None or (element in AMOUNTS and doc.get("mflag") == "P"):
            return "flagged day"
        return "rule not met"

    out = []
    for s, r in episodes:
        vals = [days[s][d] for d in r]
        thr = [thresholds[(s, d.month)] for d in r] if thresholds is not None else \
              [above if direction == "above" else below] * len(r)
        excess = [(v - t) if direction == "above" else (t - v) for v, t in zip(vals, thr)]
        ep = {
            "station": s, "name": meta[s]["name"], "area": meta[s]["area"],
            "start": r[0].date().isoformat(), "end": r[-1].date().isoformat(), "days": len(r),
            "intensity": round(sum(excess) / len(excess), 2),
            "peak": max(vals) if direction == "above" else min(vals),
            "threshold": round(sum(thr) / len(thr), 2),
            "starts_after": reason(s, r[0] - ONE_DAY), "ends_before": reason(s, r[-1] + ONE_DAY),
            "trace": {"station": s, "from": r[0].date().isoformat(), "to": r[-1].date().isoformat(),
                      "element": element},
        }
        if element in AMOUNTS:
            ep["total"] = round(sum(vals), 1)
        out.append(ep)

    out.sort(key=lambda e: (e["station"], e["start"]))
    per_year = collections.Counter(int(e["start"][:4]) for e in out)
    return {
        "service": "F2",
        "query": {"element": element, "start": start, "end": end, "area": area, "bbox": bbox, "near": near,
                  "stations": stations, "rule": {"above": above, "below": below, "pct_above": pct_above,
                                                 "pct_below": pct_below}, "min_days": min_days},
        "stations_searched": len(ids),
        "stations_with_episodes": len({e["station"] for e in out}),
        "stations_without_reference": skipped,
        "count": len(out),
        "episodes_per_year": {str(y): n for y, n in sorted(per_year.items())},
        "episodes": out,  # canonical order: station, start
        "complete": True,
    }
