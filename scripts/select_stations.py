#!/usr/bin/env python3
"""Select the WeatherScope stations from the GHCN-Daily metadata.

Rules (documented in section 2.1 of the report):
  * Michigan: US stations whose state code is MI in ghcnd-stations.txt.
  * Southern Germany: GM stations inside the borders of Bavaria (DE-BY) or
    Baden-Wuerttemberg (DE-BW), from Natural Earth admin-1 polygons.
  * Active: at least one of TMAX, TMIN, PRCP has data overlapping 1991-2020.
  * Elevation: stations above ELEV_MAX_M are excluded, so that both areas stay
    comparable to Michigan's low, flat relief (Michigan peaks at about 520 m).
  * Long record: at least one of TMAX, TMIN, PRCP spans the whole of 1991-2020
    according to ghcnd-inventory.txt. This only uses first and last years, so
    it is a candidate flag; gaps are measured once the observations are loaded.

Outputs:
  data/scope/stations.csv      one row per candidate station, with the reason
                               for exclusion when it is excluded
  data/scope/summary.json      counts per area and per element

Usage:
    python3 scripts/select_stations.py
"""
import collections
import csv
import json
import pathlib

from matplotlib.path import Path

ROOT = pathlib.Path(__file__).resolve().parent.parent
META = ROOT / "data" / "raw" / "metadata"
OUT = ROOT / "data" / "scope"

STUDY = (1991, 2020)
REFERENCE = (1961, 1990)
CORE = ("TMAX", "TMIN", "PRCP")
SNOW_ELEMENTS = ("SNOW", "SNWD")
ELEMENTS = CORE + SNOW_ELEMENTS
ELEV_MAX_M = 800.0
GERMAN_STATES = {"DE-BY": "Bavaria", "DE-BW": "Baden-Wuerttemberg"}


def read_stations():
    """Fixed-width format described in readme.txt, section IV."""
    stations = {}
    with (META / "ghcnd-stations.txt").open() as f:
        for line in f:
            stations[line[0:11]] = {
                "id": line[0:11],
                "lat": float(line[12:20]),
                "lon": float(line[21:30]),
                "elev_m": float(line[31:37]),
                "state": line[38:40].strip(),
                "name": line[41:71].strip(),
                "gsn": line[72:75].strip(),
                "wmo_id": line[80:85].strip(),
            }
    return stations


def read_inventory():
    """Fixed-width format described in readme.txt, section VII."""
    inv = collections.defaultdict(dict)
    with (META / "ghcnd-inventory.txt").open() as f:
        for line in f:
            inv[line[0:11]][line[31:35]] = (int(line[36:40]), int(line[41:45]))
    return inv


def load_polygons(codes):
    data = json.loads((META / "ne_10m_admin_1_states_provinces.geojson").read_text())
    polys = {}
    for feat in data["features"]:
        code = feat["properties"].get("iso_3166_2")
        if code not in codes:
            continue
        geom = feat["geometry"]
        parts = [geom["coordinates"]] if geom["type"] == "Polygon" else geom["coordinates"]
        polys[code] = [Path(part[0]) for part in parts]  # outer rings only
    missing = set(codes) - set(polys)
    if missing:
        raise SystemExit(f"borders not found for {sorted(missing)}")
    return polys


def overlaps(span, period):
    return span is not None and span[0] <= period[1] and span[1] >= period[0]


def covers(span, period):
    return span is not None and span[0] <= period[0] and span[1] >= period[1]


def main() -> None:
    stations = read_stations()
    inv = read_inventory()
    polys = load_polygons(GERMAN_STATES)

    rows = []
    for sid, st in stations.items():
        area = region = None
        if sid.startswith("US") and st["state"] == "MI":
            area, region = "michigan", "Michigan"
        elif sid.startswith("GM"):
            for code, label in GERMAN_STATES.items():
                if any(p.contains_point((st["lon"], st["lat"])) for p in polys[code]):
                    area, region = "southern_germany", label
                    break
        if area is None:
            continue

        spans = {e: inv[sid].get(e) for e in ELEMENTS}
        active = any(overlaps(spans[e], STUDY) for e in CORE)
        if not active:
            reason = "no TMAX/TMIN/PRCP in 1991-2020"
        elif st["elev_m"] > ELEV_MAX_M:
            reason = f"elevation above {ELEV_MAX_M:.0f} m"
        else:
            reason = ""

        row = {
            **st,
            "area": area,
            "region": region,
            "network": sid[2],  # third character of the ID, see readme.txt section IV
            "selected": reason == "",
            "excluded_reason": reason,
            "long_record": any(covers(spans[e], STUDY) for e in CORE),
            "reference_record": any(covers(spans[e], REFERENCE) for e in CORE),
        }
        for e in ELEMENTS:
            row[f"{e}_first"] = spans[e][0] if spans[e] else ""
            row[f"{e}_last"] = spans[e][1] if spans[e] else ""
        rows.append(row)

    rows.sort(key=lambda r: (r["area"], r["id"]))
    OUT.mkdir(parents=True, exist_ok=True)
    with (OUT / "stations.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)

    summary = {"study_period": STUDY, "reference_period": REFERENCE,
               "elevation_max_m": ELEV_MAX_M, "areas": {}}
    for area in ("michigan", "southern_germany"):
        cand = [r for r in rows if r["area"] == area]
        sel = [r for r in cand if r["selected"]]
        s = {
            "stations_in_area_metadata": len(cand),
            "excluded_inactive": sum(r["excluded_reason"].startswith("no ") for r in cand),
            "excluded_elevation": sum(r["excluded_reason"].startswith("elevation") for r in cand),
            "selected": len(sel),
            "selected_long_record": sum(r["long_record"] for r in sel),
            "selected_reference_record": sum(r["reference_record"] for r in sel),
            "by_network": dict(collections.Counter(r["network"] for r in sel)),
            "elements": {},
        }
        for e in ELEMENTS:
            s["elements"][e] = {
                "overlap_study": sum(overlaps(inv[r["id"]].get(e), STUDY) for r in sel),
                "cover_study": sum(covers(inv[r["id"]].get(e), STUDY) for r in sel),
                "cover_reference": sum(covers(inv[r["id"]].get(e), REFERENCE) for r in sel),
            }
        summary["areas"][area] = s
    (OUT / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
