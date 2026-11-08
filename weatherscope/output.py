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
