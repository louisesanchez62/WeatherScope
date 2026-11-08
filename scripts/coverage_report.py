#!/usr/bin/env python3
"""Measure coverage, missingness and anomalies of the loaded observations.

Reads the WeatherScope database (never the raw files), one station at a time
through the station/element/date index, and writes:

  results/coverage/stations.csv   one row per station: counts, completeness,
                                  first/last dates, longest gap, measured
                                  long-record flags
  results/coverage/summary.json   totals per area and period: stations,
                                  station-days, observations and usable
                                  observations per element, obs-time habits
  results/coverage/anomalies.json each anomaly type with its count and up to
                                  5 example records (station, date, element,
                                  raw value, flags) to check against NOAA
  results/coverage/stations_per_year.csv  stations reporting each year

Definitions (also used in the report):
  * station-day: a station and a date with at least one of TMAX, TMIN, PRCP.
  * usable observation: an observation without a quality flag.
  * valid month (WMO 3/5 rule): at most 5 missing days and at most 3
    consecutive missing days of usable values.
  * measured long record for an element: at least 80 % valid months over the
    period (288 of 360 for 1991-2020).

Usage:
    python3 scripts/coverage_report.py [--uri ...] [--db ...] [--stations ID ...]
"""
import argparse
import collections
import csv
import datetime as dt
import json
import math
import os
import pathlib
import time

import numpy as np
import pandas as pd
from pymongo import MongoClient

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "results" / "coverage"

CORE = ["TMAX", "TMIN", "PRCP"]
ELEMENTS = CORE + ["SNOW", "SNWD"]
PERIODS = {"study": ("1991-01-01", "2020-12-31"), "reference": ("1961-01-01", "1990-12-31")}
MAX_EXAMPLES = 5

# Physically implausible values for low-lying stations in our two areas, applied
# only to values NOAA did not flag. Units: degC for TMAX/TMIN, mm otherwise.
IMPLAUSIBLE = {
    "TMAX": lambda v: (v > 45) | (v < -45),
    "TMIN": lambda v: (v > 35) | (v < -50),
    "PRCP": lambda v: v > 300,
    "SNOW": lambda v: v > 1000,
    "SNWD": lambda v: v > 3000,
}


def period_mask(dates: pd.Series, period: str) -> pd.Series:
    a, b = PERIODS[period]
    return (dates >= a) & (dates <= b)


def month_validity(usable_dates: pd.DatetimeIndex, start: str, end: str) -> pd.Series:
    """True per calendar month when it passes the WMO 3/5 rule."""
    days = pd.date_range(start, end, freq="D")
    missing = pd.Series(~days.isin(usable_dates), index=days)
    month = days.to_period("M")
    run_id = (~missing).cumsum()                       # a new run starts after each present day
    runs = missing.groupby([month, run_id]).sum()      # length of each missing run, per month
    longest = runs.groupby(level=0).max()
    total = missing.groupby(month).sum()
    return (total <= 5) & (longest <= 3)


def longest_gap(dates: pd.Series) -> tuple[int, str | None, str | None]:
    """Longest stretch of days without any record, between the first and last record."""
    if len(dates) < 2:
        return 0, None, None
    d = np.sort(dates.unique())
    diffs = np.diff(d).astype("timedelta64[D]").astype(int) - 1
    i = int(diffs.argmax())
    if diffs[i] <= 0:
        return 0, None, None
    return int(diffs[i]), str(pd.Timestamp(d[i]).date()), str(pd.Timestamp(d[i + 1]).date())


class Anomalies:
    def __init__(self):
        self.count = collections.Counter()
        self.stations = collections.defaultdict(set)
        self.examples = collections.defaultdict(list)
        self.description = {}

    def add(self, key: str, description: str, rows: pd.DataFrame, station: str):
        if rows.empty:
            return
        self.description[key] = description
        self.count[key] += len(rows)
        self.stations[key].add(station)
        room = MAX_EXAMPLES - len(self.examples[key])
        clean = lambda x: None if x is None or (isinstance(x, float) and math.isnan(x)) else x
        for r in rows.head(max(room, 0)).itertuples():
            self.examples[key].append({
                "station": station, "date": str(r.date.date()), "element": r.element,
                "raw": int(r.raw), "mflag": clean(r.mflag), "qflag": clean(r.qflag),
                "sflag": clean(r.sflag), "obs_time": clean(r.obs_time), "source_file": r.file,
            })

    def to_json(self):
        return {k: {"description": self.description[k], "count": int(self.count[k]),
                    "stations": len(self.stations[k]), "examples": self.examples[k]}
                for k in sorted(self.count, key=lambda k: -self.count[k])}


def fetch_station(db, sid: str) -> pd.DataFrame:
    cur = db.observations.find(
        {"station": sid},
        {"_id": 0, "date": 1, "element": 1, "raw": 1, "value": 1, "mflag": 1,
         "qflag": 1, "sflag": 1, "obs_time": 1, "source.file": 1},
    ).hint("station_element_date")
    rows = [{**d, "file": d.pop("source")["file"]} for d in cur]
    if not rows:
        return pd.DataFrame(columns=["date", "element", "raw", "value", "mflag", "qflag",
                                     "sflag", "obs_time", "file"])
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])
    return df


def analyse_station(st: dict, df: pd.DataFrame, anomalies: Anomalies, totals, per_year, obs_times):
    sid, area = st["_id"], st["area"]
    row = {"id": sid, "name": st["name"], "area": area, "network": st["network"],
           "lat": st["location"]["coordinates"][1], "lon": st["location"]["coordinates"][0],
           "elev_m": st["elev_m"], "inventory_long_record": st["long_record"]}
    if df.empty:
        return row

    usable = df[df["qflag"].isna()]
    core = df[df["element"].isin(CORE)]

    for period in PERIODS:
        pm = period_mask(df["date"], period)
        core_days = int(core.loc[period_mask(core["date"], period), "date"].nunique())
        row[f"{period}_station_days"] = core_days
        totals[(area, period)]["station_days"] += core_days
        if core_days:
            totals[(area, period)]["stations"] += 1
        for el in ELEMENTS:
            e = df[pm & (df["element"] == el)]
            u = e[e["qflag"].isna()]
            t = totals[(area, period)][el]
            t["observations"] += int(len(e))
            t["usable"] += len(u)
            t["qflagged"] += len(e) - len(u)
            if el in ("PRCP", "SNOW"):
                t["mflag_P"] += int((e["mflag"] == "P").sum())
                t["mflag_T"] += int((e["mflag"] == "T").sum())
            if len(e):
                t["stations"] += 1
            row[f"{period}_{el}_obs"] = len(e)
            row[f"{period}_{el}_usable"] = len(u)

    # Completeness per element (WMO 3/5 rule) and measured long-record flags.
    for el in ELEMENTS:
        e = usable[usable["element"] == el]
        for period, (a, b) in PERIODS.items():
            dates = pd.DatetimeIndex(e.loc[period_mask(e["date"], period), "date"])
            if len(dates) == 0:
                row[f"{period}_{el}_valid_months"] = 0
                continue
            valid = month_validity(dates, a, b)
            row[f"{period}_{el}_valid_months"] = int(valid.sum())
        if len(e):
            row[f"{el}_first"] = str(e["date"].min().date())
            row[f"{el}_last"] = str(e["date"].max().date())
    row["study_long_record"] = any(row.get(f"study_{el}_valid_months", 0) >= 288 for el in CORE)
    row["reference_long_record"] = any(row.get(f"reference_{el}_valid_months", 0) >= 288 for el in CORE)

    s = core.loc[period_mask(core["date"], "study"), "date"]
    row["study_longest_gap_days"], row["study_longest_gap_from"], row["study_longest_gap_to"] = longest_gap(s)

    years = core["date"].dt.year.unique()
    for y in years:
        per_year[(area, int(y))] += 1

    ot = df.assign(t=df["obs_time"].fillna("(none)")).groupby(["element", "t"]).size()
    for (el, t), n in ot.items():
        obs_times[(area, el)][t] += int(n)

    # ---- anomalies
    flagged = df[df["qflag"].notna()]
    for q, g in flagged.groupby("qflag"):
        anomalies.add(f"qflag_{q}", f"Values carrying NOAA quality flag {q}", g.sort_values("date"), sid)

    for el, test in IMPLAUSIBLE.items():
        e = usable[usable["element"] == el]
        bad = e[test(e["value"])]
        anomalies.add(f"implausible_{el}", f"Unflagged {el} outside the plausible range for our areas", bad, sid)

    tx = usable[usable["element"] == "TMAX"].set_index("date")
    tn = usable[usable["element"] == "TMIN"].set_index("date")
    both = tx.join(tn, lsuffix="_max", rsuffix="_min", how="inner")
    inv = both[both["raw_max"] < both["raw_min"]]
    if not inv.empty:
        rows = df[df["element"].isin(["TMAX", "TMIN"]) & df["date"].isin(inv.index)].sort_values(["date", "element"])
        anomalies.add("tmax_below_tmin", "Unflagged TMAX lower than TMIN on the same day (both rows listed)", rows, sid)

    summer_snow = usable[(usable["element"] == "SNWD") & (usable["value"] > 0)
                         & usable["date"].dt.month.isin([6, 7, 8])]
    anomalies.add("snow_depth_in_summer", "Unflagged snow depth above zero in June, July or August", summer_snow, sid)

    prcp_p = df[(df["element"].isin(["PRCP", "SNOW"])) & (df["mflag"] == "P")]
    anomalies.add("mflag_P", "PRCP or SNOW flagged P: missing, presumed zero", prcp_p, sid)

    neg = usable[usable["element"].isin(["PRCP", "SNOW", "SNWD"]) & (usable["raw"] < 0)]
    anomalies.add("negative_amount", "Negative precipitation, snowfall or snow depth", neg, sid)

    return row


def find_colocated(stations: list[dict], km: float = 1.0) -> list[dict]:
    """Pairs of station IDs less than `km` apart: possible duplicate sources."""
    out = []
    for i, a in enumerate(stations):
        lon1, lat1 = a["location"]["coordinates"]
        for b in stations[i + 1:]:
            lon2, lat2 = b["location"]["coordinates"]
            dlat, dlon = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
            h = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
            d = 2 * 6371 * math.asin(math.sqrt(h))
            if d < km:
                out.append({"a": a["_id"], "a_name": a["name"], "b": b["_id"], "b_name": b["name"],
                            "distance_km": round(d, 3)})
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--uri", default=os.environ.get("WEATHERSCOPE_URI", "mongodb://localhost:27017"))
    ap.add_argument("--db", default=os.environ.get("WEATHERSCOPE_DB", "weatherscope"))
    ap.add_argument("--stations", nargs="+", metavar="ID", help="only these stations (for testing)")
    args = ap.parse_args()

    db = MongoClient(args.uri, serverSelectionTimeoutMS=5000)[args.db]
    query = {"_id": {"$in": args.stations}} if args.stations else {}
    stations = list(db.stations.find(query).sort("_id", 1))

    def new_period():
        d = {el: collections.Counter() for el in ELEMENTS}
        d["station_days"] = 0
        d["stations"] = 0
        return d

    totals = collections.defaultdict(new_period)
    per_year = collections.Counter()
    obs_times = collections.defaultdict(collections.Counter)
    anomalies = Anomalies()
    rows = []
    t0 = time.perf_counter()
    for i, st in enumerate(stations, 1):
        df = fetch_station(db, st["_id"])
        rows.append(analyse_station(st, df, anomalies, totals, per_year, obs_times))
        if i % 100 == 0 or i == len(stations):
            print(f"  {i}/{len(stations)} stations  {time.perf_counter() - t0:.0f}s")

    OUT.mkdir(parents=True, exist_ok=True)
    fields = sorted({k for r in rows for k in r}, key=lambda k: (k not in rows[0], k))
    with (OUT / "stations.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)

    summary = {"generated_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
               "definitions": {"station_day": "station + date with at least one of TMAX, TMIN, PRCP",
                               "usable": "no quality flag",
                               "valid_month": "<= 5 missing days and <= 3 consecutive (WMO 3/5 rule)",
                               "measured_long_record": ">= 288 valid months of TMAX, TMIN or PRCP"},
               "areas": {}}
    for area in sorted({r["area"] for r in rows}):
        a = summary["areas"][area] = {}
        sel = [r for r in rows if r["area"] == area]
        a["stations_loaded"] = len(sel)
        a["measured_long_record_study"] = sum(bool(r.get("study_long_record")) for r in sel)
        a["measured_long_record_reference"] = sum(bool(r.get("reference_long_record")) for r in sel)
        a["inventory_long_record"] = sum(bool(r["inventory_long_record"]) for r in sel)
        for period in PERIODS:
            t = totals[(area, period)]
            a[period] = {"stations_with_core_data": t["stations"], "station_days": t["station_days"],
                         "elements": {el: dict(t[el]) for el in ELEMENTS}}
        a["obs_time"] = {el: dict(obs_times[(area, el)].most_common(6)) for el in ELEMENTS}
    (OUT / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")

    anom = anomalies.to_json()
    anom["colocated_stations"] = {
        "description": "Pairs of station IDs less than 1 km apart (possible duplicate sources)",
        "pairs": find_colocated(stations)}
    anom["colocated_stations"]["count"] = len(anom["colocated_stations"]["pairs"])
    (OUT / "anomalies.json").write_text(json.dumps(anom, indent=2, default=str) + "\n")

    with (OUT / "stations_per_year.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["area", "year", "stations_with_core_data"])
        for (area, year), n in sorted(per_year.items()):
            w.writerow([area, year, n])

    print(json.dumps({k: {p: {"station_days": v[p]["station_days"]} for p in PERIODS}
                      | {"measured_long_record_study": v["measured_long_record_study"]}
                      for k, v in summary["areas"].items()}, indent=2))
    print("anomalies:", {k: v["count"] for k, v in anom.items()})
    print(f"done in {time.perf_counter() - t0:.0f}s, results in {OUT.relative_to(ROOT)}/")


if __name__ == "__main__":
    main()
