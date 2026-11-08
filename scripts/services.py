"""Profile-independent WeatherScope services used by both deployments.

The functions deliberately receive a ``Store`` instead of a PyMongo database.
All observation reads therefore go through ``Store.scope``; switching from the
single server to mongos changes placement, not the user-facing service API.
"""
from collections import defaultdict
from datetime import datetime, timezone


def _date(value: str) -> datetime:
    return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)


def _usable_filter(element: str) -> dict:
    query = {"element": element, "qflag": None}
    if element == "PRCP":
        # A trace (T) and "missing, presumed zero" (P) are not ordinary zero.
        query["mflag"] = {"$nin": ["T", "P"]}
    return query


def f1(store, *, station: str, start: str, end: str) -> dict:
    """Station time series plus explicit availability/quality accounting."""
    filt = store.scope(stations=[station], date={"$gte": _date(start), "$lte": _date(end)})
    docs = list(store.obs.find(filt, {"_id": 0}).sort([("date", 1), ("element", 1)]))
    daily: dict[str, dict] = {}
    quality = defaultdict(int)
    for doc in docs:
        day = doc["date"].date().isoformat()
        row = daily.setdefault(day, {"date": day, "values": {}})
        row["values"][doc["element"]] = {"value": doc["value"], "mflag": doc["mflag"], "qflag": doc["qflag"]}
        quality["total"] += 1
        quality["usable" if doc["qflag"] is None and not (doc["element"] == "PRCP" and doc["mflag"] in ("P", "T")) else "excluded"] += 1
    station_doc = store.stations.find_one({"_id": station}, {"_id": 1, "name": 1, "area": 1, "location": 1})
    return {"station": station_doc, "start": start, "end": end,
            "days_with_observation": len(daily), "quality": dict(sorted(quality.items())),
            "series": [daily[key] for key in sorted(daily)]}


def f3(store, *, areas: list[str], years: list[int], element: str) -> dict:
    """Rank monthly area anomalies against per-station 1961--1990 normals.

    A station contributes one monthly mean, then all eligible stations get the
    same weight in an area/month. This avoids stations with denser daily
    reporting dominating the comparison.  Months with no usable station are
    omitted rather than interpreted as normal weather.
    """
    if not years:
        raise ValueError("au moins une année est requise")
    stations = sorted(store.stations.distinct("_id", {"area": {"$in": areas}}))
    if not stations:
        raise ValueError("aucune station dans les zones demandées")
    start, end = min(years), max(years)
    filt = store.scope(areas=areas, allow_broadcast=True, **_usable_filter(element))
    # Fetching the reference and requested period in one routed request keeps
    # the semantics identical for local, hashed, and zoned deployments.
    filt["date"] = {"$gte": _date(f"1961-01-01"), "$lte": _date(f"{end}-12-31")}
    normal_values: dict[tuple[str, int], list[float]] = defaultdict(list)
    study_values: dict[tuple[str, int, int], list[float]] = defaultdict(list)
    for doc in store.obs.find(filt, {"station": 1, "area": 1, "date": 1, "value": 1}).sort([("station", 1), ("date", 1)]):
        year, month = doc["date"].year, doc["date"].month
        if 1961 <= year <= 1990:
            normal_values[(doc["station"], month)].append(doc["value"])
        if year in years:
            study_values[(doc["station"], year, month)].append(doc["value"])
    normals = {key: sum(values) / len(values) for key, values in normal_values.items()}
    grouped: dict[tuple[str, int, int], list[float]] = defaultdict(list)
    for (station, year, month), values in study_values.items():
        if (station, month) in normals:
            grouped[(store.area_of[station], year, month)].append(sum(values) / len(values) - normals[(station, month)])
    rows = [{"area": area, "year": year, "month": month, "anomaly": sum(values) / len(values),
             "stations": len(values)} for (area, year, month), values in grouped.items()]
    rows.sort(key=lambda row: (-abs(row["anomaly"]), row["area"], row["year"], row["month"]))
    return {"element": element, "years": sorted(years), "reference": "1961-1990",
            "ranking": rows}
