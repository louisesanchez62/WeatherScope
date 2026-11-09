#!/usr/bin/env python3
"""Independent verification: recompute two results from the raw NOAA files.

This script never touches MongoDB and imports nothing from `weatherscope`. It
reads the original per-station files (data/raw/by_station/*.csv.gz) with
pandas, reimplements the rules from their written definition, and compares
with the outputs the services saved in results/verification/:

  V1 (F2)  southern Germany, TMAX >= 30 degC for at least 5 consecutive days,
           summer 2003: every episode (station, start, end, length, peak).
  V2 (F3)  winter (Dec-Feb) snow-cover days, 1991-2020 against 1961-1990, both
           areas: every area-month anomaly, normal and standardised anomaly,
           and the headline percentage change per area.

Rules reimplemented here (see the report, sections 2.2 and 3.2):
  usable value  = empty quality flag (and, for PRCP/SNOW, measurement flag not P);
  valid month   = at most 5 missing days and at most 3 consecutive (WMO 3/5);
  snow-cover    = days with SNWD > 0 among usable days, scaled to the month;
  normal        = mean and sample sd over >= 24 valid reference years;
  area value    = mean of station anomalies per 1-degree cell, then cells
                  weighted by cos(latitude of the cell centre); a month counts
                  when >= 50 % of the area's cells with a normal report.

Usage:
    python3 scripts/verify_independent.py
"""
import calendar
import json
import math
import pathlib
import sys

import numpy as np
import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw" / "by_station"
SCOPE = ROOT / "data" / "scope" / "stations.csv"
SAVED = ROOT / "results" / "verification"
COLS = ["id", "date", "element", "value", "mflag", "qflag", "sflag", "obs_time"]


def read_station(sid, elements, y0, y1):
    df = pd.read_csv(RAW / f"{sid}.csv.gz", header=None, names=COLS, dtype=str, keep_default_na=False)
    df = df[df["element"].isin(elements)]
    df = df[(df["date"] >= f"{y0}0101") & (df["date"] <= f"{y1}1231")]
    df = df[df["qflag"] == ""]  # usable values only
    df["date"] = pd.to_datetime(df["date"], format="%Y%m%d")
    df["value"] = df["value"].astype(int)
    return df


# ------------------------------------------------------------------ V1 (F2)

def verify_f2(stations):
    rows = []
    for sid in stations[stations["area"] == "southern_germany"]["id"]:
        df = read_station(sid, ["TMAX"], 2003, 2003)
        df = df[(df["date"] >= "2003-06-01") & (df["date"] <= "2003-08-31") & (df["value"] >= 300)]
        days = df.sort_values("date")
        run = []
        for d, v in zip(days["date"], days["value"]):
            if run and (d - run[-1][0]).days != 1:
                rows.append((sid, run))
                run = []
            run.append((d, v))
        if run:
            rows.append((sid, run))
    mine = sorted((s, r[0][0].date().isoformat(), r[-1][0].date().isoformat(), len(r), max(v for _, v in r) / 10)
                  for s, r in rows if len(r) >= 5)
    saved = json.loads((SAVED / "service_f2_heat2003.json").read_text())
    theirs = sorted((e["station"], e["start"], e["end"], e["days"], e["peak"]) for e in saved["episodes"])
    return {"episodes_recomputed": len(mine), "episodes_service": len(theirs), "identical": mine == theirs,
            "only_recomputed": [list(x) for x in sorted(set(mine) - set(theirs))][:5],
            "only_service": [list(x) for x in sorted(set(theirs) - set(mine))][:5]}


# ------------------------------------------------------------------ V2 (F3)

def station_months(sid):
    df = read_station(sid, ["SNWD"], 1961, 2020)
    out = {}
    for (y, m), g in df.groupby([df["date"].dt.year, df["date"].dt.month]):
        dim = calendar.monthrange(y, m)[1]
        present = np.zeros(dim + 2, dtype=bool)  # padded so edge gaps count as runs
        present[0] = present[-1] = True
        present[g["date"].dt.day.to_numpy()] = True
        missing = dim - len(g)
        longest, run = 0, 0
        for p in present[1:-1]:
            run = 0 if p else run + 1
            longest = max(longest, run)
        if missing <= 5 and longest <= 3:
            out[(y, m)] = (g["value"] > 0).sum() / len(g) * dim
    return out


def verify_f3(stations):
    data = {sid: station_months(sid) for sid in stations["id"]}
    normals = {}
    for sid, sm in data.items():
        for m in (12, 1, 2):
            ref = [v for (y, mm), v in sm.items() if mm == m and 1961 <= y <= 1990]
            if len(ref) >= 24:
                normals[(sid, m)] = (float(np.mean(ref)), float(np.std(ref, ddof=1)))
    meta = stations.set_index("id")
    # merge stations under 1 km apart, keeping the one with most normals (none happen to qualify in practice)
    count = pd.Series([s for s, _ in normals]).value_counts()
    kept = []
    for sid in sorted(count.index, key=lambda s: (-count[s], s)):
        lat, lon = meta.loc[sid, "lat"], meta.loc[sid, "lon"]
        if all(6371 * 2 * math.asin(math.sqrt(math.sin(math.radians(meta.loc[k, "lat"] - lat) / 2) ** 2
               + math.cos(math.radians(lat)) * math.cos(math.radians(meta.loc[k, "lat"]))
               * math.sin(math.radians(meta.loc[k, "lon"] - lon) / 2) ** 2)) >= 1.0 for k in kept):
            kept.append(sid)
    kept = set(kept)

    def cell(sid):
        return (math.floor(meta.loc[sid, "lat"]), math.floor(meta.loc[sid, "lon"]))

    eligible = {}
    for (sid, m) in normals:
        if sid in kept:
            eligible.setdefault((meta.loc[sid, "area"], m), set()).add(cell(sid))

    results = {}
    for area in ("michigan", "southern_germany"):
        for y in range(1990, 2021):
            for m in (12, 1, 2):
                if not (1991 <= y <= 2020):
                    continue
                cells = {}
                for sid in sorted(kept):
                    if meta.loc[sid, "area"] != area or (sid, m) not in normals or (y, m) not in data[sid]:
                        continue
                    mean, sd = normals[(sid, m)]
                    v = data[sid][(y, m)]
                    cells.setdefault(cell(sid), []).append((v - mean, mean, (v - mean) / sd if sd > 0 else None))
                n_elig = len(eligible.get((area, m), ()))
                if not cells or len(cells) / n_elig < 0.5:
                    continue
                w = {c: math.cos(math.radians(c[0] + 0.5)) for c in cells}
                tot = sum(w.values())
                an = sum(w[c] * np.mean([a for a, _, _ in r]) for c, r in cells.items()) / tot
                no = sum(w[c] * np.mean([n for _, n, _ in r]) for c, r in cells.items()) / tot
                zc = {c: [zz for _, _, zz in r if zz is not None] for c, r in cells.items()}
                zw = sum(w[c] for c in cells if zc[c])  # cells whose stations all have sd = 0 give no z
                z = sum(w[c] * np.mean(zc[c]) for c in cells if zc[c]) / zw if zw else None
                results[(area, f"{y}-{m:02d}")] = (an, no, z)

    saved = json.loads((SAVED / "service_f3_snwd_djf.json").read_text())
    theirs = {(r["area"], r["month"]): (r["anomaly"], r["normal"], r["z"]) for r in saved["all_months"]}
    common = results.keys() & theirs.keys()
    worst = max(max(abs(a - b) for a, b in zip(results[k], theirs[k]) if a is not None and b is not None)
                for k in common) if common else None
    headline = {}
    for area in ("michigan", "southern_germany"):
        mine = [results[k] for k in results if k[0] == area]
        srv = [theirs[k] for k in theirs if k[0] == area]
        headline[area] = {"recomputed_pct": round(100 * float(np.mean([a for a, _, _ in mine]) / np.mean([n for _, n, _ in mine])), 1),
                          "service_pct": round(100 * float(np.mean([a for a, _, _ in srv]) / np.mean([n for _, n, _ in srv])), 1)}
    return {"area_months_recomputed": len(results), "area_months_service": len(theirs),
            "same_months": bool(results.keys() == theirs.keys()),
            "largest_difference": round(float(worst), 4) if worst is not None else None,
            "within_rounding": bool(worst is not None and worst <= 0.011), "headline_snow_cover_change": headline}


def main() -> None:
    stations = pd.read_csv(SCOPE)
    stations = stations[stations["selected"]]
    print("V1 (F2) recomputing 2003 heat episodes from raw files...")
    v1 = verify_f2(stations)
    print(json.dumps(v1, indent=2))
    print("V2 (F3) recomputing winter snow-cover anomalies from raw files...")
    v2 = verify_f3(stations)
    print(json.dumps(v2, indent=2))
    out = {"V1_f2_heat_2003": v1, "V2_f3_snow_cover_djf": v2}
    (SAVED / "independent_verification.json").write_text(json.dumps(out, indent=2) + "\n")
    ok = v1["identical"] and v2["same_months"] and v2["within_rounding"]
    print("\nVERIFIED" if ok else "\nMISMATCH")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
