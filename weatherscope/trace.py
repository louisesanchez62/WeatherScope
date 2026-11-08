"""List the source records behind a result.

A result from any service can be traced by giving either the observation IDs
(<station>:<YYYY-MM-DD>:<element>) or a station, a date range and elements.
Each record is returned as stored: raw value as in the NOAA file, converted
value, the three flags, observation time, source file and import batch. With
history=True, earlier versions replaced by F5 are listed too.
"""
import datetime as dt

from . import ELEMENTS

FIELDS = {"_id": 1, "station": 1, "date": 1, "element": 1, "raw": 1, "value": 1,
          "mflag": 1, "qflag": 1, "sflag": 1, "obs_time": 1, "source": 1}


def _record(d: dict) -> dict:
    return {
        "id": d["_id"], "station": d["station"], "date": d["date"].date().isoformat(),
        "element": d["element"], "raw": d["raw"], "value": d["value"],
        "mflag": d["mflag"], "qflag": d["qflag"], "sflag": d["sflag"], "obs_time": d["obs_time"],
        "source_file": d["source"]["file"], "batch": d["source"]["batch"],
    }


def trace(db, ids=None, station=None, start=None, end=None, elements=ELEMENTS, history=False, limit=5000) -> dict:
    if ids:
        stations = sorted({i.split(":", 1)[0] for i in ids})
        q = {"station": {"$in": stations}, "_id": {"$in": list(ids)}}
        query = {"ids": list(ids)}
    elif station and start and end:
        q = {"station": station, "element": {"$in": list(elements)},
             "date": {"$gte": dt.datetime.fromisoformat(start), "$lte": dt.datetime.fromisoformat(end)}}
        query = {"station": station, "start": start, "end": end, "elements": list(elements)}
    else:
        raise ValueError("give observation IDs, or a station with a start and end date")

    cur = db.observations.find(q, FIELDS).sort([("station", 1), ("date", 1), ("element", 1)]).limit(limit + 1)
    records = [_record(d) for d in cur]
    truncated = len(records) > limit
    records = records[:limit]

    out = {"service": "trace", "query": query, "count": len(records), "truncated": truncated,
           "records": records, "complete": True}
    if ids:
        found = {r["id"] for r in records}
        out["not_found"] = sorted(set(ids) - found)
    if history:
        rid = [r["id"] for r in records]
        out["history"] = [{
            "id": h["obs_id"], "replaced_at": h["replaced_at"].isoformat(timespec="seconds"),
            "by_batch": h["by_batch"], "previous_raw": h["previous"]["raw"],
            "previous_mflag": h["previous"]["mflag"], "previous_qflag": h["previous"]["qflag"],
            "previous_batch": h["previous"]["source"]["batch"],
        } for h in db.history.find({"obs_id": {"$in": rid}}).sort([("obs_id", 1), ("replaced_at", 1)])]
    return out
