#!/usr/bin/env python3
"""Plot one station's winters (December-February) from the F1 service.

Calls F1 by winter for SNOW, SNWD and TMIN, saves the F1 result as JSON, and
draws three panels sharing the time axis: snowfall total, days with snow on
the ground, and mean minimum temperature. Winters failing the WMO 3/5 rule
are left out. Dashed lines give the average over the reference winters
(1961-62 to 1989-90) and the study winters (1990-91 to 2019-20).

Usage:
    python3 scripts/plot_station_winters.py [--station USW00094847] [--from 1960-12-01] [--to 2020-02-29]
"""
import argparse
import json
import pathlib
import statistics
import sys

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from weatherscope.db import connect  # noqa: E402
from weatherscope.f1 import station_summary  # noqa: E402

STUDY_FIRST = 1990  # winter 1990-91 is the first study winter
COLORS = {"reference": "#9aa3ab", "study": "#1f6f9a"}
PANELS = [
    ("SNOW", "total", "Chutes de neige (mm)", 1),
    ("SNWD", "days_positive", "Jours avec neige au sol", 1),
    ("TMIN", "mean", "TMIN moyenne (°C)", 1),
]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--station", default="USW00094847")
    ap.add_argument("--from", dest="start", default="1960-12-01")
    ap.add_argument("--to", dest="end", default="2020-02-29")
    args = ap.parse_args()

    result = station_summary(connect(), args.station, args.start, args.end, "winter", ["SNOW", "SNWD", "TMIN"])
    out_json = ROOT / "results" / "f1" / f"{args.station}_winters.json"
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(result, indent=2, default=str) + "\n")

    fig, axes = plt.subplots(3, 1, figsize=(7, 5.2), sharex=True)
    summary = {}
    for ax, (el, key, label, _) in zip(axes, PANELS):
        rows = [r for r in result["periods"] if r["element"] == el and r["valid"] and r[key] is not None]
        xs = [int(r["period"][:4]) for r in rows]
        ys = [r[key] for r in rows]
        colors = [COLORS["study" if x >= STUDY_FIRST else "reference"] for x in xs]
        if el == "TMIN":
            ax.plot(xs, ys, color="#444", lw=0.8, zorder=1)
            ax.scatter(xs, ys, c=colors, s=12, zorder=2)
        else:
            ax.bar(xs, ys, color=colors, width=0.8)
        for period, (lo, hi) in {"reference": (0, STUDY_FIRST - 1), "study": (STUDY_FIRST, 9999)}.items():
            vals = [y for x, y in zip(xs, ys) if lo <= x <= hi]
            if vals:
                m = statistics.mean(vals)
                summary[f"{el}_{key}_{period}"] = {"mean": round(m, 1), "winters": len(vals)}
                xr = [x for x in xs if lo <= x <= hi]
                ax.hlines(m, min(xr) - 0.4, max(xr) + 0.4, colors="#b26a12", linestyles="--", lw=1.2, zorder=3)
                left = period == "reference"
                ax.text(min(xr) - 0.8 if left else max(xr) + 0.8, m, f"{m:.0f}" if el != "TMIN" else f"{m:.1f}",
                        va="center", ha="right" if left else "left", fontsize=7, color="#b26a12")
        ax.set_ylabel(label, fontsize=8)
        ax.tick_params(labelsize=7)
        ax.grid(axis="y", color="#e3e3e3", lw=0.6)
        ax.set_axisbelow(True)
    axes[-1].set_xlabel("Hiver (année de décembre)", fontsize=8)
    axes[0].set_title(f"{result['station']['name']} ({args.station}) : hivers de décembre à février\n"
                      "gris : référence 1961–1990 · bleu : étude 1991–2020 · pointillés : moyennes",
                      fontsize=8.5)
    fig.tight_layout()
    out = ROOT / "rapport" / "assets" / f"hivers_{args.station}"
    fig.savefig(out.with_suffix(".pdf"), bbox_inches="tight", pad_inches=0.02)
    fig.savefig(out.with_suffix(".png"), dpi=160, bbox_inches="tight", pad_inches=0.02)
    print(json.dumps(summary, indent=2))
    print(f"wrote {out.with_suffix('.pdf').relative_to(ROOT)}, {out_json.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
