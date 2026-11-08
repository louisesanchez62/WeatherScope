#!/usr/bin/env python3
"""Draw the two study areas side by side, on the same latitude range and scale,
with every candidate station pinned.

Reads data/scope/stations.csv (from select_stations.py) and the Natural Earth
borders in data/raw/metadata/. Writes rapport/assets/carte_zones.pdf (for
LaTeX) and carte_zones.png (for quick viewing). Labels are in French because
the figure goes into the report.

Usage:
    python3 scripts/plot_station_maps.py
"""
import csv
import json
import math
import pathlib
import statistics

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402
from matplotlib.patches import Polygon  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
META = ROOT / "data" / "raw" / "metadata"
SCOPE = ROOT / "data" / "scope" / "stations.csv"
OUT = ROOT / "rapport" / "assets" / "carte_zones"

LAT_RANGE = (41.3, 50.8)
VIEWS = {
    "michigan": {"lon": (-90.7, -82.1), "title": "Michigan (États-Unis)",
                 "codes": {"US-MI"}, "color": "#1f6f9a"},
    "southern_germany": {"lon": (7.2, 14.1), "title": "Bavière et Bade-Wurtemberg (Allemagne)",
                         "codes": {"DE-BY", "DE-BW"}, "color": "#b26a12"},
}
WATER, LAND, BORDER = "#eaf2f7", "#f2f2ef", "#b9bfc4"
HIGHLIGHT = {"michigan": "#c5dbe9", "southern_germany": "#f4e6d2"}
ANNOTATE = {"USW00094847": "Detroit Metro AP"}


def rings(geom):
    polys = [geom["coordinates"]] if geom["type"] == "Polygon" else geom["coordinates"]
    return [p[0] for p in polys]


def bbox_hits(ring, lon, lat):
    xs = [p[0] for p in ring]
    ys = [p[1] for p in ring]
    return max(xs) >= lon[0] and min(xs) <= lon[1] and max(ys) >= lat[0] and min(ys) <= lat[1]


def main() -> None:
    borders = json.loads((META / "ne_10m_admin_1_states_provinces.geojson").read_text())["features"]
    lakes = json.loads((META / "ne_10m_lakes.geojson").read_text())["features"]
    with SCOPE.open() as f:
        stations = list(csv.DictReader(f))

    widths = [VIEWS[a]["lon"][1] - VIEWS[a]["lon"][0] for a in VIEWS]
    aspect = 1 / math.cos(math.radians(sum(LAT_RANGE) / 2))
    fig, axes = plt.subplots(1, 2, figsize=(10, 7.2), gridspec_kw={"width_ratios": widths})

    medians = {a: statistics.median(float(s["lat"]) for s in stations
                                    if s["area"] == a and s["selected"] == "True") for a in VIEWS}

    for ax, (area, v) in zip(axes, VIEWS.items()):
        ax.set_facecolor(WATER)
        for feat in borders:
            code = feat["properties"].get("iso_3166_2")
            for ring in rings(feat["geometry"]):
                if not bbox_hits(ring, v["lon"], LAT_RANGE):
                    continue
                fill = HIGHLIGHT[area] if code in v["codes"] else LAND
                lw = 0.9 if code in v["codes"] else 0.4
                ax.add_patch(Polygon(ring, closed=True, facecolor=fill,
                                     edgecolor="#6d767d" if code in v["codes"] else BORDER,
                                     linewidth=lw, zorder=1))

        for feat in lakes:
            for ring in rings(feat["geometry"]):
                if bbox_hits(ring, v["lon"], LAT_RANGE):
                    ax.add_patch(Polygon(ring, closed=True, facecolor=WATER,
                                         edgecolor="#9fb6c4", linewidth=0.4, zorder=2))

        pts = [s for s in stations if s["area"] == area]
        recent = [s for s in pts if s["selected"] == "True" and s["long_record"] == "False"]
        longr = [s for s in pts if s["selected"] == "True" and s["long_record"] == "True"]
        high = [s for s in pts if s["excluded_reason"].startswith("elevation")]
        ax.scatter([float(s["lon"]) for s in recent], [float(s["lat"]) for s in recent],
                   s=7, facecolors="none", edgecolors=v["color"], linewidths=0.5, alpha=0.6, zorder=3)
        ax.scatter([float(s["lon"]) for s in longr], [float(s["lat"]) for s in longr],
                   s=16, color=v["color"], edgecolors="white", linewidths=0.4, zorder=4)
        ax.scatter([float(s["lon"]) for s in high], [float(s["lat"]) for s in high],
                   s=18, marker="x", color="#444", linewidths=0.8, zorder=4)

        for sid, label in ANNOTATE.items():
            for s in pts:
                if s["id"] == sid:
                    ax.annotate(label, (float(s["lon"]), float(s["lat"])), xytext=(-118, -24),
                                textcoords="offset points", fontsize=7.5,
                                bbox={"facecolor": "white", "edgecolor": "none", "alpha": 0.85, "pad": 1.5},
                                arrowprops={"arrowstyle": "-", "lw": 0.6, "color": "#333"}, zorder=6)

        for a2, lat in medians.items():
            ax.axhline(lat, color=VIEWS[a2]["color"], lw=0.9, ls="--", zorder=5)

        ax.set_xlim(*v["lon"])
        ax.set_ylim(*LAT_RANGE)
        ax.set_aspect(aspect)
        ax.set_yticks(range(42, 51, 2))
        ax.set_yticklabels([f"{t}°N" for t in range(42, 51, 2)], fontsize=8)
        xt = [t for t in range(math.ceil(v["lon"][0]), math.floor(v["lon"][1]) + 1) if t % 2 == 0]
        ax.set_xticks(xt)
        ax.set_xticklabels([f"{abs(t)}°{'O' if t < 0 else 'E'}" for t in xt], fontsize=8)
        ax.grid(color="white", lw=0.6, zorder=1.5)

        sub = f"{len(longr)} à longue série · {len(recent)} autres actives"
        if high:
            sub += f" · {len(high)} exclues"
        ax.set_title(f"{v['title']}\n{sub}", fontsize=9.5)

    handles = [
        Line2D([], [], marker="o", ls="", color="#555", markeredgecolor="white", markersize=5,
               label="Station à longue série (TMAX, TMIN ou PRCP sur 1991–2020)"),
        Line2D([], [], marker="o", ls="", markerfacecolor="none", markeredgecolor="#555", markersize=4,
               label="Autre station active sur 1991–2020"),
        Line2D([], [], marker="x", ls="", color="#444", markersize=5, label="Exclue : altitude > 800 m"),
        Line2D([], [], color=VIEWS["michigan"]["color"], ls="--",
               label=f"Latitude médiane des stations, Michigan ({medians['michigan']:.1f}°N)"),
        Line2D([], [], color=VIEWS["southern_germany"]["color"], ls="--",
               label=f"Latitude médiane des stations, sud de l'Allemagne ({medians['southern_germany']:.1f}°N)"),
    ]
    fig.legend(handles=handles, loc="lower center", ncol=2, fontsize=7.5, frameon=False)
    fig.tight_layout(rect=(0, 0.08, 1, 1), w_pad=0.5)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT.with_suffix(".pdf"), bbox_inches="tight", pad_inches=0.02)
    fig.savefig(OUT.with_suffix(".png"), dpi=160, bbox_inches="tight", pad_inches=0.02)
    print(f"wrote {OUT.with_suffix('.pdf').relative_to(ROOT)} and .png")


if __name__ == "__main__":
    main()
