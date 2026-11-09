"""Print service results as plain-text tables or as JSON."""
import json


def _fmt(v) -> str:
    if v is None:
        return "-"
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, float):
        return f"{v:g}"
    return str(v)


def table(rows: list[dict], columns: list[str]) -> str:
    if not rows:
        return "(no rows)"
    cells = [[_fmt(r.get(c)) for c in columns] for r in rows]
    widths = [max(len(c), *(len(row[i]) for row in cells)) for i, c in enumerate(columns)]
    line = lambda vals: "  ".join(v.rjust(w) if v.replace(".", "").lstrip("-").isdigit() else v.ljust(w)
                                  for v, w in zip(vals, widths)).rstrip()
    return "\n".join([line(columns), line(["-" * w for w in widths])] + [line(r) for r in cells])


def as_json(result: dict) -> str:
    return json.dumps(result, indent=2, ensure_ascii=False, default=str)


ELEMENT_COLUMNS = {
    "TMAX": ["mean", "min", "max"],
    "TMIN": ["mean", "min", "max"],
    "PRCP": ["total", "days_positive"],
    "SNOW": ["total", "days_positive"],
    "SNWD": ["days_positive", "max", "mean"],
}


def print_result(result: dict) -> None:
    svc = result["service"]
    if svc == "stations":
        print(f"{result['count']} stations" + (" (list truncated)" if result["truncated"] else ""))
        cols = ["id", "name", "area", "lat", "lon", "elev_m", "network", "inventory_long_record"]
        if result["stations"] and "distance_km" in result["stations"][0]:
            cols.insert(2, "distance_km")
        rows = [{**s, "elements": " ".join(f"{k}:{v}" for k, v in s["elements"].items())} for s in result["stations"]]
        print(table(rows, cols + ["elements"]))
    elif svc == "F1":
        s, q = result["station"], result["query"]
        print(f"{s['id']}  {s['name']}  ({s['region']}, {s['lat']:.4f}, {s['lon']:.4f}, {_fmt(s['elev_m'])} m, network {s['network']})")
        print(f"{q['start']} to {q['end']}, by {q['by']}\n")
        print("Overall availability")
        print(table([{"element": k, **v} for k, v in result["overall"].items()],
                    ["element", "usable_days", "coverage", "valid_periods", "periods_with_data", "qflagged", "mflag_P"]))
        base = ["period", "expected_days", "usable", "coverage", "valid", "qflagged", "mflag_P", "mflag_T"]
        for el in q["elements"]:
            rows = [r for r in result["periods"] if r["element"] == el]
            if rows:
                print(f"\n{el}")
                print(table(rows, base + ELEMENT_COLUMNS[el]))
    elif svc == "F2":
        q = result["query"]
        rule = next(f"{k.replace('_', '-')} {v:g}" for k, v in q["rule"].items() if v is not None)
        print(f"F2 {q['element']} {rule}, at least {q['min_days']} day(s), {q['start']} to {q['end']}")
        print(f"{result['count']} episodes at {result['stations_with_episodes']} of "
              f"{result['stations_searched']} stations")
        if result["stations_without_reference"]:
            print(f"{len(result['stations_without_reference'])} stations skipped: "
                  f"fewer than 60 reference values for some months")
        top = result.get("display_top") or 20
        ranked = sorted(result["episodes"], key=lambda e: (-e["days"], -e["intensity"], e["station"], e["start"]))
        cols = ["station", "name", "start", "end", "days", "intensity", "peak", "threshold"]
        if ranked and "total" in ranked[0]:
            cols.append("total")
        print(f"\nLongest episodes (top {min(top, len(ranked))}, then highest intensity)")
        print(table(ranked[:top], cols + ["starts_after", "ends_before"]))
        if result["episodes_per_year"]:
            print("\nEpisodes per start year")
            print("  ".join(f"{y}:{n}" for y, n in result["episodes_per_year"].items()))
    elif svc == "F3":
        q = result["query"]
        print(f"F3 {q['element']}: months {q['years'][0]}-{q['years'][1]} against the {q['reference'][0]}-"
              f"{q['reference'][1]} normals, {q['grid_deg']:g} degree cells"
              + (f", calendar months {q['months']}" if q["months"] != list(range(1, 13)) else ""))
        print(f"station-months used: {result['reference_station_months']:,} reference, "
              f"{result['study_station_months']:,} study; stations with a normal: "
              + ", ".join(f"{a} {n}" for a, n in result["stations_with_normal"].items())
              + f"; {len(result['colocated_dropped'])} co-located stations merged")
        print(f"{result['months_ranked']} area-months ranked, "
              f"{len(result['months_excluded_low_coverage'])} left out for low cell coverage")
        cols = ["month", "anomaly", "z"] + (["pct_of_normal"] if q["element"] in ("PRCP", "SNOW") else []) + \
               ["stations", "cells", "eligible_cells", "strongest_station"]
        for area, rows in result["per_area"].items():
            print(f"\n{area}")
            print(table([{**r, "strongest_station": f"{r['evidence'][0]['station']} ({r['evidence'][0]['z']:+})"
                          if r["evidence"] and r["evidence"][0]["z"] is not None else "-"} for r in rows], cols))
    elif svc == "F4":
        q = result["query"]
        rule = next(f"{k.replace('_', '-')} {v:g}" for k, v in q["rule"].items() if v is not None)
        print(f"F4 {q['element']} {rule}, {q['start']} to {q['end']}: regional = at least "
              f"{q['min_share']:.0%} of reporting {q['grid_deg']:g} degree cells and {q['min_cells']} cells, "
              f"+/-{q['tolerance_days']} day")
        print(f"{result['stations_used']} stations, {result['days_with_data']} days with data; events: "
              + ", ".join(f"{n} {k}" for k, n in sorted(result["count_by_scope"].items())))
        by_start = {(e["start"], e["scope"]): e for e in result["events"]}
        for scope, starts in result["ranking"].items():
            rows = [{**by_start[(s, scope)], "top_stations": ", ".join(
                        f"{x['name']} ({x['max_excess']:+g})" for x in by_start[(s, scope)]["evidence"][:2])}
                    for s in starts]
            print(f"\n{scope.capitalize()} events, most severe first")
            print(table(rows, ["start", "end", "days", "peak_day", "peak_share", "peak_cells", "reporting_cells",
                               "stations", "severity", "extreme_value", "top_stations"]))
    elif svc == "F5":
        c = result["counts"]
        print(f"batch {result['batch']} ({result['status']}): {result['files']} files, "
              f"{result['start']} to {result['end']}, {result['elapsed_s']:.0f}s")
        print(f"inserted {c['inserted']:,}  unchanged {c['unchanged']:,}  revised {c['revised']:,}  "
              f"rejected {c['rejected']:,}")
        print(f"stations with rows: {result['stations_with_rows']}, rows by period: {result['rows_by_period']}")
        if result.get("read_model_months_refreshed") is not None:
            print(f"monthly read model: {result['read_model_months_refreshed']:,} station-months rebuilt "
                  f"in {result['read_model_refresh_s']} s")
        for k, v in result["notes"].items():
            print(f"  {k}: {v:,}")
    elif svc == "trace":
        print(f"{result['count']} records" + (" (truncated)" if result["truncated"] else ""))
        print(table(result["records"], ["station", "date", "element", "raw", "value", "mflag", "qflag",
                                        "sflag", "obs_time", "source_file", "batch"]))
        if result.get("not_found"):
            print(f"\nnot found: {', '.join(result['not_found'])}")
        if "history" in result:
            print(f"\n{len(result['history'])} earlier versions")
            if result["history"]:
                print(table(result["history"], ["id", "replaced_at", "by_batch", "previous_raw",
                                                "previous_mflag", "previous_qflag", "previous_batch"]))
    else:
        print(as_json(result))
