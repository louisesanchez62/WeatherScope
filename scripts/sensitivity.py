#!/usr/bin/env python3
"""Does the headline result depend on our parameter choices?

Headline: winter (Dec-Feb) snow-cover days fall much more in southern Germany
than in Michigan between 1961-1990 and 1991-2020, while winter TMIN warms a
little less there. This reruns F3 with other values for the three parameters
that shape it:
  * elevation cap:            800 m (default) and 600 m
  * grid cell size:           1 deg (default), 0.5 deg and 2 deg
  * valid years for a normal: 24 (default), 20 and 27
The station-month aggregation (the slow part) runs once per element and is
reused by every variant. Writes results/sensitivity/winter.json.

Usage:
    python3 scripts/sensitivity.py
"""
import datetime as dt
import json
import pathlib
import statistics
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from weatherscope import AREAS  # noqa: E402
from weatherscope.db import connect  # noqa: E402
from weatherscope.f3 import _station_months, monthly_anomalies  # noqa: E402

OUT = ROOT / "results" / "sensitivity" / "winter.json"
VARIANTS = [
    ("baseline (800 m, 1°, 24 years)", {}),
    ("elevation cap 600 m", {"max_elev": 600}),
    ("grid 0.5°", {"grid": 0.5}),
    ("grid 2°", {"grid": 2.0}),
    ("normal needs 20 years", {"min_ref_years": 20}),
    ("normal needs 27 years", {"min_ref_years": 27}),
]


def summarise(result, element):
    out = {}
    for area in AREAS:
        ms = [m for m in result["all_months"] if m["area"] == area]
        if not ms:
            continue
        anomaly = statistics.fmean(m["anomaly"] for m in ms)
        normal = statistics.fmean(m["normal"] for m in ms)
        out[area] = {"stations": result["stations_with_normal"][area], "months": len(ms),
                     "mean_anomaly": round(anomaly, 2)}
        if element == "SNWD":
            out[area]["pct_change"] = round(100 * anomaly / normal, 1)
            out[area]["days_per_winter"] = round(3 * anomaly, 1)
    return out


def main() -> None:
    db = connect()
    results = {"generated_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "variants": {}}
    for element in ("SNWD", "TMIN"):
        t = time.perf_counter()
        sm = _station_months(db, AREAS, element, 1961, 2020)
        print(f"{element}: {len(sm):,} station-months in {time.perf_counter() - t:.0f}s")
        for label, params in VARIANTS:
            r = monthly_anomalies(db, element, months=[12, 1, 2], station_months=sm, **params)
            results["variants"].setdefault(label, {"params": params})[element] = summarise(r, element)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(results, indent=2) + "\n")

    print(f"\n{'variant':34} {'MI snow cover':>14} {'DE snow cover':>14} {'MI TMIN':>8} {'DE TMIN':>8}   stations MI/DE (SNWD)")
    for label, v in results["variants"].items():
        s, t = v["SNWD"], v["TMIN"]
        print(f"{label:34} {s['michigan']['pct_change']:>+13.1f}% {s['southern_germany']['pct_change']:>+13.1f}% "
              f"{t['michigan']['mean_anomaly']:>+7.2f}° {t['southern_germany']['mean_anomaly']:>+7.2f}°   "
              f"{s['michigan']['stations']}/{s['southern_germany']['stations']}")


if __name__ == "__main__":
    main()
