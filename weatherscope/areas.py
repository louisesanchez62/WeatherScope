"""Geographic area filters for the `stations` collection.

An area is given in one of three ways:
  * a named area:       michigan, southern_germany
  * a bounding box:     lon_min,lat_min,lon_max,lat_max
  * a radius:           lon,lat,km
"""
import math

from . import AREAS

EARTH_KM = 6371.0


def station_filter(area: str | None = None, bbox: str | None = None, near: str | None = None) -> dict:
    """Build a MongoDB filter on `stations` (uses the 2dsphere index for bbox/near)."""
    q = {}
    if area:
        if area not in AREAS:
            raise ValueError(f"unknown area {area!r}; choose one of {', '.join(AREAS)}")
        q["area"] = area
    if bbox:
        x1, y1, x2, y2 = (float(v) for v in bbox.split(","))
        ring = [[x1, y1], [x2, y1], [x2, y2], [x1, y2], [x1, y1]]
        q["location"] = {"$geoWithin": {"$geometry": {"type": "Polygon", "coordinates": [ring]}}}
    if near:
        lon, lat, km = (float(v) for v in near.split(","))
        q["location"] = {"$geoWithin": {"$centerSphere": [[lon, lat], km / EARTH_KM]}}
    return q


def distance_km(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    dlat, dlon = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    h = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    return 2 * EARTH_KM * math.asin(math.sqrt(h))
