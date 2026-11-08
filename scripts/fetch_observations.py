#!/usr/bin/env python3
"""Download the GHCN-Daily per-station files for the selected stations.

Reads data/scope/stations.csv and downloads
https://www.ncei.noaa.gov/pub/data/ghcn/daily/by_station/<ID>.csv.gz
into data/raw/by_station/. Each file is recorded in
data/raw/by_station/MANIFEST.json with its URL, download time (UTC), size and
SHA-256. Files already in the manifest are skipped, so an interrupted run can
simply be started again. Use --force to download everything again (for example
to pick up NOAA revisions for an F5 test).

Usage:
    python3 scripts/fetch_observations.py [--long-only] [--stations ID ...] [--workers 6] [--force]
"""
import argparse
import concurrent.futures as cf
import csv
import datetime as dt
import hashlib
import json
import pathlib
import time
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCOPE = ROOT / "data" / "scope" / "stations.csv"
OUT = ROOT / "data" / "raw" / "by_station"
MANIFEST = OUT / "MANIFEST.json"
BASE = "https://www.ncei.noaa.gov/pub/data/ghcn/daily/by_station/"


def selected_stations(long_only: bool) -> list[str]:
    with SCOPE.open() as f:
        rows = [r for r in csv.DictReader(f) if r["selected"] == "True"]
    if long_only:
        rows = [r for r in rows if r["long_record"] == "True"]
    return [r["id"] for r in rows]


def download(sid: str) -> tuple[str, dict | None, str]:
    url = f"{BASE}{sid}.csv.gz"
    path = OUT / f"{sid}.csv.gz"
    tmp = path.with_suffix(".gz.part")
    for attempt in range(4):
        try:
            h = hashlib.sha256()
            with urllib.request.urlopen(url, timeout=120) as r, tmp.open("wb") as f:
                while chunk := r.read(1 << 20):
                    f.write(chunk)
                    h.update(chunk)
            tmp.replace(path)
            return sid, {
                "url": url,
                "retrieved_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                "bytes": path.stat().st_size,
                "sha256": h.hexdigest(),
            }, ""
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return sid, None, "not found on NOAA server"
            err = f"HTTP {e.code}"
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
            err = str(e)
        time.sleep(2 ** attempt)
    tmp.unlink(missing_ok=True)
    return sid, None, err


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--long-only", action="store_true", help="only stations flagged long_record")
    ap.add_argument("--stations", nargs="+", metavar="ID", help="only these station IDs")
    ap.add_argument("--workers", type=int, default=6, help="parallel downloads (default 6)")
    ap.add_argument("--force", action="store_true", help="download again even if already in the manifest")
    args = ap.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    manifest = json.loads(MANIFEST.read_text()) if MANIFEST.exists() else {}
    wanted = args.stations or selected_stations(args.long_only)
    todo = [s for s in wanted if args.force or f"{s}.csv.gz" not in manifest
            or not (OUT / f"{s}.csv.gz").exists()]
    print(f"{len(wanted)} stations wanted, {len(wanted) - len(todo)} already downloaded, {len(todo)} to fetch")

    failed = {}
    done = 0
    with cf.ThreadPoolExecutor(max_workers=args.workers) as pool:
        for sid, entry, err in pool.map(download, todo):
            done += 1
            if entry:
                manifest[f"{sid}.csv.gz"] = entry
            else:
                failed[sid] = err
            if done % 50 == 0 or done == len(todo):
                MANIFEST.write_text(json.dumps(manifest, indent=1, sort_keys=True) + "\n")
                print(f"  {done}/{len(todo)} done, {len(failed)} failed")

    MANIFEST.write_text(json.dumps(manifest, indent=1, sort_keys=True) + "\n")
    total = sum(e["bytes"] for k, e in manifest.items() if k[:-7] in set(wanted))
    print(f"manifest has {len(manifest)} files; wanted stations total {total / 1e6:.1f} MB on disk")
    for sid, err in sorted(failed.items()):
        print(f"FAILED {sid}: {err}")


if __name__ == "__main__":
    main()
