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
python3 scripts/build_read_model.py                   # monthly read model used by F3, about 10 minutes
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

### `f2`: episodes of consecutive days (F2)

```bash
# summer 2003 heatwave in southern Germany: TMAX >= 30 degC for at least 5 days
python3 -m weatherscope f2 --area southern_germany --element TMAX --above 30 --min-days 5 --from 2003-06-01 --to 2003-08-31
# days below freezing at Detroit Metro: the 30 Dec 2013 - 9 Jan 2014 cold spell is one episode
python3 -m weatherscope f2 --station USW00094847 --element TMAX --below 0 --min-days 3 --from 2013-11-01 --to 2014-03-31
# heavy rain relative to each station's own climate (99th percentile of wet days, 1961-1990)
python3 -m weatherscope f2 --area michigan --element PRCP --pct-above 99 --min-days 2 --from 2008-01-01 --to 2012-12-31
```

One rule per query: `--above X`, `--below X` (°C or mm), or `--pct-above P` / `--pct-below P` (the station's own 1961–1990 percentile for that calendar month; for PRCP and SNOW, over days with at least 1 mm). Stations without at least 60 reference values for a month are skipped and counted.

Only usable days count: a day with a quality flag, a P value or no record at all breaks the episode. Episodes run across year boundaries. Each episode gives the station, start, end, length, intensity (mean amount beyond the threshold), peak, threshold, total (PRCP/SNOW), why it started and ended (`rule not met`, `missing day`, `flagged day`, `query limit`), and the arguments for `trace` to list its records. `--json` returns every episode, sorted by station and start date.

### `f3`: most unusual months per area (F3)

```bash
# warmest and coldest months of 1991-2020 in both areas, against 1961-1990
python3 -m weatherscope f3 --element TMAX
# winter snow cover only (December-February), least snowy months first
python3 -m weatherscope f3 --element SNWD --months 12 1 2 --sign low
# a single area, a shorter period, finer grid
python3 -m weatherscope f3 --element PRCP --area southern_germany --from-year 2000 --to-year 2010 --grid 0.5
```

How it works:
1. MongoDB builds station-months from usable values only (no quality flag, no P): mean temperature, precipitation or snowfall total scaled to the full month, or days with snow on the ground. A month counts only if it passes the WMO 3/5 rule.
2. Each station gets a normal per calendar month from the reference years (default 1961–1990), if it has at least 24 valid years (`--min-ref-years`).
3. Each station-month gets an anomaly (value − normal) and a standardised anomaly z = anomaly / standard deviation, plus % of normal for PRCP and SNOW.
4. Uneven station density: stations less than 1 km apart count once, station anomalies are averaged per grid cell (`--grid`, default 1°), then cells are averaged with a cos(latitude) weight. A month is ranked only if at least half of the area's cells with a normal have data (`--min-cells`).
5. Months are ranked by |z|, or by z with `--sign high|low`.

Each ranked month lists its stations (value, normal, anomaly, z) and the dates to pass to `trace`. Station-months are read from the monthly read model `obs_months` when it exists (`--source auto`, the default), otherwise from `observations` (`--source observations`); both give identical results. A 60-year query over both areas takes about 10 seconds on the read model, 2–4 minutes without it.

### `f4`: regional events versus isolated extremes (F4)

```bash
# the 2003 heatwave covers every grid cell of southern Germany
python3 -m weatherscope f4 --area southern_germany --element TMAX --above 30 --from 2003-06-01 --to 2003-08-31
# heavy snow in Michigan: regional blizzards (Feb 2011, Feb 2015) versus lake-effect bands
python3 -m weatherscope f4 --area michigan --element SNOW --above 150 --from 2010-11-01 --to 2015-03-31
```

Same rule options as `f2`. Stations are grouped in grid cells (`--grid`, default 1°, stations under 1 km apart count once). Each day, F4 compares the cells where at least one station meets the rule (on that day or within `--tolerance` days, default 1, to absorb observation-time shifts) with the cells that report usable data, weighted by cell area. A missing station leaves the count instead of counting as "not affected". Days are then classified:

- **regional**: at least `--min-share` (default 25 %) of reporting cells and `--min-cells` (default 2) affected;
- **local**: 2 or more cells, below that share;
- **isolated**: a single cell, with at least 3 cells reporting.

Consecutive days of the same kind form an event, with its peak share, cells, stations, severity (sum of daily share × mean excess beyond the threshold), extreme value, the stations involved and their dates, and the arguments for `trace`.

### `f5`: import a batch of NOAA observations (F5)

```bash
# add 2021-2025 from the downloaded per-station files
python3 -m weatherscope f5 --label f5-2021-2025 --from 2021-01-01 --to 2025-12-31
# import an extract supplied separately (per-station or per-year NOAA file)
python3 -m weatherscope f5 --label extract --files 2021.csv.gz --from 2021-01-01 --to 2021-12-31
# add stations left out of the initial load, e.g. above 800 m
python3 -m weatherscope f5 --label high-stations --include-unselected --station GM000004155
```

An observation is identified by station + date + element. For each row: new → inserted; same value and flags → unchanged (nothing written); different value or flag → the stored version is copied to `history`, then replaced (revised); unreadable row or second row for the same observation in the batch → rejected. Importing the same files again therefore gives `inserted 0, revised 0`. Each import is recorded in `batches` with its files and their SHA-256, and timings go to `results/ingest/`. The other services read the archive at query time, and F5 rebuilds the months it changed in the monthly read model, so every service reflects an import immediately. `scripts/load_observations.py` runs the same code.

### `trace`: list the source records behind a result

```bash
python3 -m weatherscope trace --station USW00094847 --from 2014-01-05 --to 2014-01-07
python3 -m weatherscope trace --id USW00094847:2014-01-06:TMIN --history
```

Shows each record as stored (raw NOAA value, converted value, flags, observation time, source file, import batch). `--history` also lists versions replaced by a later import.
