# WeatherScope

Historical weather observatory built on NOAA GHCN-Daily and MongoDB, for the M2 SLCD Big Data & NoSQL project. Scope: Michigan and southern Germany (Bavaria + Baden-Württemberg, below 800 m), 1991–2020, with 1961–1990 as reference.

## Setup

Needs Python 3.10+, MongoDB 8.0 and about 2 GB of disk.

```bash
pip install -r requirements.txt
scripts/local_server.sh start                         # Part I server, port 27017, 3 GB cache
python3 scripts/fetch_metadata.py                     # NOAA metadata + borders
python3 scripts/select_stations.py                    # data/scope/stations.csv
python3 scripts/fetch_observations.py                 # 1,353 station files, ~215 MB
python3 scripts/init_db.py                            # collections, validators, indexes
python3 scripts/load_observations.py --label initial  # ~31.6 M observations, about 1 hour
```

Details: [`scripts/README.md`](scripts/README.md) for the pipeline, [`schema/README.md`](schema/README.md) for the data model.

## Using WeatherScope

```bash
python3 -m weatherscope <command> [options]
```

Every command takes `--json` for machine-readable output and `--uri` / `--db` to pick the server (or `WEATHERSCOPE_URI` / `WEATHERSCOPE_DB`).

### `stations`: find stations in an area (F1)

```bash
python3 -m weatherscope stations --area southern_germany --element SNWD
python3 -m weatherscope stations --near=-83.35,42.21,15            # lon,lat,km around Detroit Metro
python3 -m weatherscope stations --bbox=-84,42,-83,43 --name detroit
```

Write `--near=` and `--bbox=` with an equals sign: otherwise a negative longitude is read as an option.

### `f1`: summary of one station over a date range (F1)

```bash
python3 -m weatherscope f1 USW00094847 --from 2013-12-01 --to 2014-02-28
python3 -m weatherscope f1 USW00094847 --from 1990-12-01 --to 2020-02-29 --by winter --element SNWD SNOW
```

For each element and each month, year or winter (December–February), it gives availability (expected days, usable days, coverage, whether the period passes the WMO 3/5 rule), quality (values with a quality flag, P and T flags) and statistics computed on usable values only: mean/min/max temperature, precipitation and snowfall totals, days with snow on the ground and snow depth. Values flagged P are never counted as zeros.

### `trace`: list the source records behind a result

```bash
python3 -m weatherscope trace --station USW00094847 --from 2014-01-05 --to 2014-01-07
python3 -m weatherscope trace --id USW00094847:2014-01-06:TMIN --history
```

Shows each record as stored (raw NOAA value, converted value, flags, observation time, source file, import batch). `--history` also lists versions replaced by a later import.
