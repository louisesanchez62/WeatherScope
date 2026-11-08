# Scripts

Run them from the repository root. They need Python 3.10+ and the packages in `requirements.txt` (`pip install -r requirements.txt`). The database scripts also need a running MongoDB.

```bash
python3 scripts/fetch_metadata.py
python3 scripts/select_stations.py
python3 scripts/plot_station_maps.py
python3 scripts/init_db.py --uri mongodb://localhost:27017 --db weatherscope
python3 scripts/check_schema.py --uri mongodb://localhost:27017
```

| Script | What it does | Writes |
|---|---|---|
| `fetch_metadata.py` | Downloads the NOAA GHCN-Daily station list, inventory and readmes, plus the Natural Earth borders and lakes. Records URL, download time and SHA-256 for each file. Skips files already downloaded unless `--force` is given. | `data/raw/metadata/` (not in git) and `data/raw/metadata/MANIFEST.json` |
| `select_stations.py` | Selects the stations: Michigan (state code `MI`) and southern Germany (inside Bavaria or Baden-Württemberg), with TMAX, TMIN or PRCP data in 1991–2020, at most 800 m high. Flags stations covering the whole of 1991–2020 (long record) and 1961–1990 (reference). | `data/scope/stations.csv`, `data/scope/summary.json` |
| `plot_station_maps.py` | Draws both areas side by side at the same scale, with every station pinned. This is Figure 1 of the report. | `rapport/assets/carte_zones.pdf` and `.png` |
| `init_db.py` | Creates the four collections with the validators and indexes from `schema/`. Safe to re-run; `--drop` empties the collections first. | MongoDB database (default `weatherscope`) |
| `check_schema.py` | Checks that `schema/examples/valid/` is accepted and `schema/examples/invalid/` is rejected, in a temporary database it drops afterwards. | nothing |

The long-record flag uses only the first and last year in NOAA's inventory. Gaps inside the period are checked once the observations are downloaded.

The data model itself is described in [`schema/README.md`](../schema/README.md).
