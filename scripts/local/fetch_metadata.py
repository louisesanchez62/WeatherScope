#!/usr/bin/env python3
"""Download the GHCN-Daily metadata files and the state borders used for the scope.

Every file is written to data/raw/metadata/ and recorded in MANIFEST.json with
its source URL, retrieval time (UTC), size and SHA-256 checksum, as required by
the provenance section of the brief. Files already present are kept unless
--force is given, so the manifest always describes the files on disk.

Usage:
    python3 scripts/fetch_metadata.py [--force]
"""
import argparse
import datetime as dt
import hashlib
import json
import pathlib
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "raw" / "metadata"
MANIFEST = OUT / "MANIFEST.json"

NOAA = "https://www.ncei.noaa.gov/pub/data/ghcn/daily/"
SOURCES = {
    "ghcnd-stations.txt": NOAA + "ghcnd-stations.txt",
    "ghcnd-inventory.txt": NOAA + "ghcnd-inventory.txt",
    "readme.txt": NOAA + "readme.txt",
    "readme-by_year.txt": NOAA + "readme-by_year.txt",
    # Natural Earth 1:10m admin-1 borders (public domain), pinned to a release tag.
    "ne_10m_admin_1_states_provinces.geojson": (
        "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/"
        "v5.1.2/geojson/ne_10m_admin_1_states_provinces.geojson"
    ),
    # Lakes, so the Great Lakes appear on the Michigan map.
    "ne_10m_lakes.geojson": (
        "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/"
        "v5.1.2/geojson/ne_10m_lakes.geojson"
    ),
}


def sha256(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--force", action="store_true", help="re-download files already present")
    args = ap.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    manifest = json.loads(MANIFEST.read_text()) if MANIFEST.exists() else {}

    for name, url in SOURCES.items():
        path = OUT / name
        if path.exists() and name in manifest and not args.force:
            print(f"keep     {name}")
            continue
        print(f"download {name}")
        tmp = path.with_suffix(path.suffix + ".part")
        with urllib.request.urlopen(url, timeout=120) as r, tmp.open("wb") as f:
            while chunk := r.read(1 << 20):
                f.write(chunk)
        tmp.replace(path)
        manifest[name] = {
            "url": url,
            "retrieved_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            "bytes": path.stat().st_size,
            "sha256": sha256(path),
        }

    MANIFEST.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    print(f"manifest {MANIFEST.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
