#!/usr/bin/env python3
"""Automated equivalence check: same requests, same answers on every deployment.

Runs representative requests through the normal command-line entry point
(`weatherscope.__main__.execute`) on the local server and on each cluster
profile, puts every answer in canonical form (JSON with sorted keys, lists
already in each service's canonical order, ties broken deterministically) and
compares them with the local answer. Several requests span shards: the area
and multi-area requests on the hashed profile, the two-area requests on the
zoned profile.

Usage (from the repository root, both deployments running and loaded):
    python3 -m scripts.distributed.compare [hashed zoned] [--db NAME] [--heavy]
"""
import argparse
import json
import pathlib
import sys

from weatherscope.__main__ import execute

OUT = pathlib.Path(__file__).resolve().parents[2] / "results" / "part2"

REQUESTS = {
    "stations_area": ["stations", "--area", "michigan"],
    "f1_one_station_winters": ["f1", "USW00094847", "--from", "1990-12-01", "--to", "2020-02-29", "--by", "winter"],
    "f2_heat_2003_germany": ["f2", "--area", "southern_germany", "--element", "TMAX", "--above", "30",
                             "--min-days", "5", "--from", "2003-06-01", "--to", "2003-08-31"],
    "f3_snow_cover_both_areas": ["f3", "--element", "SNWD", "--months", "12", "1", "2"],
    "f4_heavy_snow_michigan": ["f4", "--area", "michigan", "--element", "SNOW", "--above", "150",
                               "--from", "2014-11-01", "--to", "2015-03-31"],
    "trace_detroit_storm": ["trace", "--station", "USW00094847", "--from", "2014-01-05", "--to", "2014-01-07"],
}
HEAVY = {  # F3 on the observations themselves (window functions across shards), slower
    "f3_tmax_observations_germany": ["f3", "--element", "TMAX", "--area", "southern_germany",
                                     "--from-year", "2003", "--to-year", "2003", "--source", "observations"],
}


def canonical(result) -> str:
    return json.dumps(result, sort_keys=True, default=str, ensure_ascii=False)


def answers(profile, db, requests):
    out = {}
    for name, argv in requests.items():
        extra = ["--profile", profile, "--json"] + (["--db", db] if db else [])
        out[name] = execute(argv + extra)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("profiles", nargs="*", default=["hashed", "zoned"])
    ap.add_argument("--db", help="base database name (zoned uses NAME_zoned); default: the profiles' own")
    ap.add_argument("--heavy", action="store_true", help="also compare F3 computed on the observations")
    a = ap.parse_args()
    requests = REQUESTS | (HEAVY if a.heavy else {})

    def db_for(profile):
        if not a.db:
            return None
        return f"{a.db}_zoned" if profile == "zoned" else a.db

    reference = answers("local", db_for("local"), requests)
    incomplete = [n for n, r in reference.items() if not r.get("complete")]
    if incomplete:
        raise SystemExit(f"the local answers are incomplete for {incomplete}: nothing to compare")
    OUT.mkdir(parents=True, exist_ok=True)
    report, failures = {}, 0
    for profile in a.profiles:
        actual = answers(profile, db_for(profile), requests)
        report[profile] = {}
        for name in requests:
            same = canonical(actual[name]) == canonical(reference[name])
            report[profile][name] = "identical" if same else "DIFFERENT"
            failures += not same
            if not same:
                (OUT / f"diff_{profile}_{name}.json").write_text(json.dumps(
                    {"local": reference[name], profile: actual[name]}, indent=1, default=str))
        print(f"{profile}: " + ", ".join(f"{n} {v}" for n, v in report[profile].items()))
    (OUT / "equivalence.json").write_text(json.dumps(report, indent=2) + "\n")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
