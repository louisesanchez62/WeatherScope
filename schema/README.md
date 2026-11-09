# Data schema

This folder is the shared contract for the WeatherScope database. The loader, the services and the report all follow it. If one of us needs to change it, change the files here, update the examples, run the check, and tell the other.

| File | Content |
|---|---|
| `observations.schema.json` | Validator for `observations` |
| `stations.schema.json` | Validator for `stations` |
| `history.schema.json` | Validator for `history` |
| `batches.schema.json` | Validator for `batches` |
| `indexes.json` | Indexes per collection, with the service each one serves |
| `examples/valid/` | Documents that must be accepted |
| `examples/invalid/` | Documents that must be rejected |

The validators are MongoDB `$jsonSchema` documents, applied with `validationLevel: strict` and `validationAction: error`: a bad document is refused, not just logged.

## Collections

| Collection | One document per | Written by |
|---|---|---|
| `stations` | station in our scope | loader, from `data/scope/stations.csv` |
| `observations` | station + date + element | loader and F5 |
| `history` | replaced version of an observation | F5, when a value or flag changes |
| `batches` | import run | loader and F5 |
| `obs_months` | station + element + month (read model) | `build_read_model.py`, then F5 |

## `observations`

| Field | Type | Rule |
|---|---|---|
| `_id` | string | `<station>:<YYYY-MM-DD>:<element>`, e.g. `USW00094847:2014-01-06:TMIN`. This is the identity of an observation. |
| `station` | string | 11-character GHCN ID |
| `area` | string | `michigan` or `southern_germany`, copied from the station |
| `date` | date | midnight UTC of the calendar date in the file |
| `element` | string | one of `TMAX`, `TMIN`, `PRCP`, `SNOW`, `SNWD`; other elements are not loaded |
| `raw` | int | value exactly as in the NOAA file |
| `value` | double | `raw / 10` for TMAX, TMIN (°C) and PRCP (mm); `raw` for SNOW, SNWD (mm) |
| `mflag`, `qflag`, `sflag` | string or null | one character, `null` when blank (never `""`) |
| `obs_time` | string or null | `HHMM` as in the file, `null` when blank |
| `source.file` | string | NOAA file the row came from |
| `source.batch` | string | `batches._id` of the import that wrote this version |

## `stations`

| Field | Type | Rule |
|---|---|---|
| `_id` | string | GHCN ID |
| `name` | string | as in `ghcnd-stations.txt` |
| `area` | string | `michigan` or `southern_germany` |
| `region` | string | `Michigan`, `Bavaria` or `Baden-Wuerttemberg` |
| `location` | GeoJSON point | `{"type": "Point", "coordinates": [lon, lat]}`, longitude first |
| `elev_m` | double or null | `null` when NOAA gives -999.9 |
| `network` | string | 3rd character of the ID: `1` CoCoRaHS, `C` COOP, `W` WBAN, `E` ECA&D, `M` WMO… |
| `long_record` | bool | TMAX, TMIN or PRCP spans all of 1991–2020 |
| `reference_record` | bool | TMAX, TMIN or PRCP spans all of 1961–1990 |
| `elements` | object | `{"TMAX": {"first": 1958, "last": 2026}, …}`, from the inventory; missing elements are left out |

## `history` and `batches`

`history` keeps the old `raw`, `value`, flags, `obs_time` and `source` of an observation, with the `obs_id`, the time it was replaced and the batch that replaced it.

`batches` records each import: an `_id` like `2026-11-09-initial`, status, start and end times, every file with its SHA-256 and row count, and the counts `inserted`, `unchanged`, `revised`, `rejected`.

## Rules the loader must follow

The validator checks types and formats. These rules it cannot check, so the loader has to apply them:

1. Build `_id` from `station`, `date` and `element`, in that format. `scripts/check_schema.py` checks it on the examples.
2. Load only the five elements, and only stations listed as `selected` in `data/scope/stations.csv`.
3. Keep every row, including flagged ones. Flags are handled in the queries:
   - a non-null `qflag` excludes the value from analyses, but it stays stored and traceable;
   - `mflag` `P` (missing, presumed zero) and `T` (trace) are never counted as ordinary zeros.
4. F5 import policy, per observation:
   - new `_id` → insert, count as `inserted`;
   - same `_id`, same `raw` and flags → do nothing, count as `unchanged`;
   - same `_id`, different `raw` or flag → copy the old version to `history`, write the new one, count as `revised`.

   Importing the same batch twice must therefore end with `inserted: 0` and `revised: 0`.

## Monthly read model: `obs_months`

Derived from `observations`, never written directly. One document per station, element and month, with the month's days in a sorted array:

```json
{ "_id": "USW00094847:SNWD:2014-01", "station": "USW00094847", "area": "michigan",
  "element": "SNWD", "year": 2014, "month": 1, "n": 31,
  "days": [ { "d": 1, "raw": 25, "v": 25.0, "m": null, "q": null, "s": "W", "t": null }, ... ] }
```

`observations` stays the source of truth (imports, revisions, history, validation, trace). `obs_months` serves regional analyses that read whole months (F3), about 13 times faster (Part I, `results/part1/compare_designs.json`). It is built once with `scripts/build_read_model.py`; afterwards every F5 import rebuilds the months it inserted into or revised. Indexes: `station, element, year, month` and `area, element, year, month`.

## Indexes

| Collection | Index | Used by |
|---|---|---|
| observations | `station, element, date` | F1, F2 per station, tracing a result |
| observations | `area, element, date` | F3, F4 |
| observations | `source.batch` | F5 checks |
| stations | `location` (2dsphere) | F1, F4: stations within an area |
| stations | `area` | listing an area's stations |
| history | `obs_id, replaced_at` | past versions of one observation |

## Examples

The valid observations and the station are real NOAA data from Detroit Metro Airport (`USW00094847`) during the 5–7 January 2014 snowstorm: 259 mm of snow on 5 January, −25.5 °C minimum on 6 January, and a trace of precipitation (`mflag` `T`) on 4 January. The batch uses the real checksum and row count of `USW00094847.csv.gz` downloaded on 8 November 2026. The history document is made up to show the format; no real revision has been seen yet.

The invalid examples are the mistakes we most want the validator to catch: an element outside our five, a converted value in `raw`, blank flags stored as `""`, the date stored as a string, a missing `source`, and a station with `lat`/`lon` fields instead of a GeoJSON point.

## Commands

```bash
pip install -r requirements.txt
python3 scripts/init_db.py --uri mongodb://localhost:27017 --db weatherscope
python3 scripts/check_schema.py --uri mongodb://localhost:27017
```

`init_db.py` can be run again at any time: it updates the validators and adds missing indexes without touching the data. `--drop` deletes the collections first. `check_schema.py` works in its own temporary database and drops it at the end.
