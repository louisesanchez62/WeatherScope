# Scripts

Run them from the repository root. They need Python 3.10+, the packages in `requirements.txt` (`pip install -r requirements.txt`) and MongoDB 8.0.

## Full pipeline

```bash
python3 scripts/fetch_metadata.py        # NOAA metadata + borders
python3 scripts/select_stations.py       # data/scope/stations.csv
python3 scripts/plot_station_maps.py     # Figure 1 of the report
python3 scripts/fetch_observations.py    # one CSV per selected station
scripts/local_server.sh start            # Part I: one mongod on port 27017
python3 scripts/init_db.py               # collections, validators, indexes
python3 scripts/load_observations.py --label initial
```

The database scripts use `mongodb://localhost:27017` and the database `weatherscope` by default. Change them with `--uri` / `--db`, or with the `WEATHERSCOPE_URI` / `WEATHERSCOPE_DB` environment variables.

## What each script does

| Script | What it does | Writes |
|---|---|---|
| `fetch_metadata.py` | Downloads the NOAA station list, inventory and readmes, plus the Natural Earth borders and lakes. Records URL, download time and SHA-256 for each file. Skips files already downloaded unless `--force`. | `data/raw/metadata/` (not in git), its `MANIFEST.json` (in git) |
| `select_stations.py` | Selects the stations: Michigan (state code `MI`) and southern Germany (inside Bavaria or Baden-Württemberg), with TMAX, TMIN or PRCP data in 1991–2020, at most 800 m high. Flags stations covering all of 1991–2020 (long record) and 1961–1990 (reference). | `data/scope/stations.csv`, `data/scope/summary.json` |
| `plot_station_maps.py` | Draws both areas side by side at the same scale, with every station pinned. | `rapport/assets/carte_zones.pdf` and `.png` |
| `fetch_observations.py` | Downloads `by_station/<ID>.csv.gz` for every selected station, 6 at a time, with retries. Records URL, date and SHA-256. Can be stopped and restarted. Options: `--long-only`, `--stations ID ...`, `--force`. | `data/raw/by_station/` (not in git), its `MANIFEST.json` (in git) |
| `local_server.sh` | `start`, `stop` or `status` of the Part I MongoDB server. | `data/db/local/`, `data/db/local.log` (not in git) |
| `init_db.py` | Creates the four collections with the validators and indexes from `schema/`. Safe to re-run; `--drop` empties the collections first. | MongoDB |
| `check_schema.py` | Checks that `schema/examples/valid/` is accepted and `schema/examples/invalid/` is rejected, in a temporary database. | nothing |
| `load_observations.py` | Loads GHCN CSV files as one import batch (see below). Also upserts the selected stations. | MongoDB, `results/ingest/<batch>.json` |
| `coverage_report.py` | Reads the database and measures coverage per area, period and element (station-days, usable values, flags, obs times), month completeness with the WMO 3/5 rule, the measured long-record flag (≥ 80 % valid months), gaps, and anomalies with real example rows. | `results/coverage/` (`summary.json`, `stations.csv`, `anomalies.json`, `stations_per_year.csv`) |

Run `plot_station_maps.py` again after `coverage_report.py`: the map then uses the measured long-record flag instead of the inventory estimate.

## Loading and updating (F5)

Every run of `load_observations.py` is a batch named `<today>-<label>`. The same command does the initial load and later updates. For each row:

- new observation → inserted;
- same values and flags as stored → unchanged, nothing is written;
- different value or flag → the old version goes to `history`, the new one replaces it (revised);
- unreadable row, or a second row for the same observation in the batch → rejected.

Loading the same files again gives `inserted: 0, revised: 0`. Examples:

```bash
# initial load, 1961-2020, all downloaded stations
python3 scripts/load_observations.py --label initial

# F5: add 2021 from a per-year file supplied separately
python3 scripts/load_observations.py --label f5-2021 --files 2021.csv.gz --end 2021-12-31
```

`--files` accepts per-station and per-year NOAA files (gzipped or not); rows from stations outside our scope are skipped. The timings and counts in `results/ingest/` are the raw measurements for experiment E1.

The data model is described in [`schema/README.md`](../schema/README.md).
