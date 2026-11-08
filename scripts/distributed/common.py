"""Shared distributed storage access and failure-safe request execution.

The application always connects to a single endpoint (the local mongod or
``mongos``).  It never opens a client connection to a shard for normal work.
That is important: mongos can route targeted requests and combine results,
while the application retains the same API in every deployment.
"""
import os
from collections.abc import Iterable

from pymongo import MongoClient
from pymongo.errors import PyMongoError

PROFILES = {
    "local": {"uri": "mongodb://localhost:27017", "db": "weatherscope", "key": ("station",)},
    "hashed": {"uri": "mongodb://localhost:27400", "db": "weatherscope", "key": ("station",)},
    "zoned": {"uri": "mongodb://localhost:27400", "db": "weatherscope_zoned", "key": ("area", "station")},
}


class Store:
    def __init__(self, profile: str | None = None):
        self.name = profile or os.getenv("WS_PROFILE", "local")
        if self.name not in PROFILES:
            raise ValueError(f"profil inconnu: {self.name}")
        p = PROFILES[self.name]
        self.uri, self.db_name, self.key = p["uri"], p["db"], p["key"]
        # majority is meaningful once shards are replica sets; with this lab's
        # one-member replica sets it still makes the acknowledgement explicit.
        self.client = MongoClient(self.uri, w="majority", retryWrites=True,
                                  serverSelectionTimeoutMS=5000, socketTimeoutMS=120000)
        self.db = self.client[self.db_name]
        self.obs = self.db.observations
        self.stations, self.history, self.batches = (self.db[n] for n in ("stations", "history", "batches"))
        self._areas: dict[str, str] | None = None

    @property
    def area_of(self) -> dict[str, str]:
        if self._areas is None:
            self._areas = {d["_id"]: d["area"] for d in self.stations.find({}, {"area": 1})}
        return self._areas

    def scope(self, *, stations: Iterable[str] | None = None,
              areas: Iterable[str] | None = None, allow_broadcast: bool = False, **extra) -> dict:
        """Build the only observation routing filter used by the services.

        ``allow_broadcast`` documents intentional all-shard work (F3/F4). A
        single station is targeted in the hashed layout.  In the zoned layout,
        adding its denormalised area supplies the shard-key prefix.
        """
        result: dict = {}
        station_list = sorted(set(stations or ()))
        area_list = sorted(set(areas or ()))
        if station_list:
            result["station"] = station_list[0] if len(station_list) == 1 else {"$in": station_list}
            if self.name == "zoned" and not area_list:
                unknown = set(station_list) - set(self.area_of)
                if unknown:
                    raise ValueError(f"station(s) absente(s): {sorted(unknown)}")
                area_list = sorted({self.area_of[s] for s in station_list})
        if area_list:
            result["area"] = area_list[0] if len(area_list) == 1 else {"$in": area_list}
        if not result and not allow_broadcast:
            raise ValueError("une requête observations doit préciser station ou area")
        return result | extra


def shards_touched(explain: object) -> list[str]:
    names: set[str] = set()
    def visit(value: object) -> None:
        if isinstance(value, dict):
            if isinstance(value.get("shardName"), str): names.add(value["shardName"])
            if isinstance(value.get("shards"), dict): names.update(value["shards"])
            for child in value.values(): visit(child)
        elif isinstance(value, list):
            for child in value: visit(child)
    visit(explain)
    return sorted(names)


def shard_status(store: Store) -> dict[str, dict]:
    """Health check every data-serving shard through its advertised endpoint."""
    if store.name == "local":
        return {"local": {"host": "localhost:27017", "up": True}}
    result = {}
    for shard in store.client.config.shards.find({}, {"_id": 1, "host": 1}):
        host = shard["host"].split("/", 1)[-1].split(",", 1)[0]
        try:
            MongoClient(f"mongodb://{host}/?directConnection=true", serverSelectionTimeoutMS=800).admin.command("ping")
            up = True
        except PyMongoError:
            up = False
        result[shard["_id"]] = {"host": host, "up": up}
    return result


def station_shards(store: Store) -> dict[str, str]:
    """Map a station to its owning chunk without querying observations."""
    if store.name == "local": return {station: "local" for station in store.area_of}
    meta = store.client.config.collections.find_one({"_id": f"{store.db_name}.observations"})
    if not meta: raise RuntimeError("observations n'est pas shardée: exécutez setup_db.py avant le chargement")
    key = list(meta["key"])
    if meta["key"].get("station") == "hashed":
        points = {d["_id"]: (d["h"],) for d in store.stations.aggregate(
            [{"$project": {"h": {"$toHashedIndexKey": "$_id"}}}])}
    else:
        points = {station: (area, station) for station, area in store.area_of.items()}
    chunks = [(tuple(c["min"][field] for field in key), tuple(c["max"][field] for field in key), c["shard"])
              for c in store.client.config.chunks.find({"uuid": meta["uuid"]})]
    return {station: next(shard for lower, upper, shard in chunks if lower <= point < upper)
            for station, point in points.items()}


def run(store: Store, service, needed_stations: Iterable[str], **params) -> dict:
    """Never label a result complete when a required owner is unavailable."""
    envelope = {"profile": store.name, "service": service.__name__}
    needed = sorted(set(needed_stations))
    if store.name != "local":
        owners = station_shards(store)
        health = shard_status(store)
        down = sorted(name for name, state in health.items() if not state["up"])
        lost = [station for station in needed if owners.get(station) in down]
        if lost:
            return envelope | {"status": "UNAVAILABLE", "complete": False,
                "missing": {"shards": down, "n_stations": len(lost), "examples": lost[:10],
                            "areas": sorted({store.area_of[s] for s in lost})},
                "message": "Résultat refusé: des observations requises sont injoignables."}
    try:
        return envelope | {"status": "OK", "complete": True, "data": service(store, **params)}
    except PyMongoError as exc:
        return envelope | {"status": "ERROR", "complete": False,
                           "message": f"{type(exc).__name__}: {exc}"}
