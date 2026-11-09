"""F1, first half: search stations within a geographic area."""
from .areas import distance_km, station_filter


def search_stations(db, area=None, bbox=None, near=None, element=None, name=None, limit=200) -> dict:
    """Stations matching the area and filters, sorted by ID (or by distance with `near`).

    `element` keeps stations whose inventory lists that element; `name` is a
    case-insensitive substring of the station name.
    """
    q = station_filter(area, bbox, near)
    if element:
        q[f"elements.{element}"] = {"$exists": True}
    if name:
        q["name"] = {"$regex": name, "$options": "i"}

    rows = []
    for s in db.stations.find(q):
        lon, lat = s["location"]["coordinates"]
        row = {
            "id": s["_id"], "name": s["name"], "area": s["area"], "region": s["region"],
            "lat": lat, "lon": lon, "elev_m": s["elev_m"], "network": s["network"],
            "inventory_long_record": s["long_record"],
            "elements": {e: f"{v['first']}-{v['last']}" for e, v in sorted(s["elements"].items())},
        }
        if near:
            nlon, nlat, _ = (float(v) for v in near.split(","))
            row["distance_km"] = round(distance_km(nlon, nlat, lon, lat), 1)
        rows.append(row)

    rows.sort(key=lambda r: (r.get("distance_km", 0), r["id"]))
    return {
        "service": "stations",
        "query": {"area": area, "bbox": bbox, "near": near, "element": element, "name": name},
        "count": len(rows),
        "stations": rows[:limit],
        "truncated": len(rows) > limit,
        "complete": True,
    }
