"""Deployments (Part II): where the services run, and refusing partial answers.

Three profiles share the same services and the same queries:

  local    one mongod (Part I), port 27017, database `weatherscope`
  hashed   cluster through mongos (port 27400), database `weatherscope`,
           shard key {station: "hashed"}
  zoned    same cluster, database `weatherscope_zoned`, shard key
           {area: 1, station: 1}, Michigan on shard1RS, Germany on shard2RS

The application only ever talks to one endpoint (mongod or mongos). Before a
read, `guard()` works out which shard owns each station the request needs
(from config.chunks, without reading observations) and pings those shards
directly. If one is down, the service is not run and the caller receives an
explicit UNAVAILABLE answer saying what is missing; a partial result is never
labelled complete. If the metadata itself cannot be read (primary shard or
config server down), the answer is UNAVAILABLE too.

Adapted from Louise's scripts/distributed/common.py (Store, run, shard
helpers), moved into the package so every service (F1-F5, trace) uses it.
"""
from pymongo import MongoClient
from pymongo.errors import PyMongoError

from .areas import station_filter

PROFILES = {
    "local": {"uri": "mongodb://localhost:27017", "db": "weatherscope"},
    "hashed": {"uri": "mongodb://localhost:27400", "db": "weatherscope"},
    "zoned": {"uri": "mongodb://localhost:27400", "db": "weatherscope_zoned"},
}
SHARDED = ("observations", "obs_months")


def profile_db(profile: str, db: str | None = None):
    """Database handle for a profile; `db` overrides the profile's database name."""
    if profile not in PROFILES:
        raise ValueError(f"unknown profile {profile!r}; choose one of {', '.join(PROFILES)}")
    p = PROFILES[profile]
    client = MongoClient(p["uri"], serverSelectionTimeoutMS=5000, socketTimeoutMS=600000, w="majority")
    return client[db or p["db"]]


def is_cluster(db) -> bool:
    try:
        return db.client.admin.command("hello").get("msg") == "isdbgrid"
    except PyMongoError:
        return False


def shard_status(db) -> dict:
    """Ping every data-serving shard through its own endpoint (the only direct connection)."""
    out = {}
    for shard in db.client.config.shards.find({}, {"_id": 1, "host": 1}):
        host = shard["host"].split("/", 1)[-1].split(",", 1)[0]
        try:
            MongoClient(f"mongodb://{host}/?directConnection=true",
                        serverSelectionTimeoutMS=800).admin.command("ping")
            up = True
        except PyMongoError:
            up = False
        out[shard["_id"]] = {"host": host, "up": up}
    return out


def station_shards(db, collection: str = "observations") -> dict:
    """Station -> owning shard, from the chunk ranges of `collection`."""
    meta = db.client.config.collections.find_one({"_id": f"{db.name}.{collection}"})
    if not meta:
        raise RuntimeError(f"{db.name}.{collection} is not sharded (run scripts/distributed/setup_db.py)")
    key = list(meta["key"])
    if meta["key"].get("station") == "hashed":
        points = {d["_id"]: (d["h"],) for d in db.stations.aggregate(
            [{"$project": {"h": {"$toHashedIndexKey": "$_id"}}}])}
    else:
        points = {d["_id"]: (d["area"], d["_id"]) for d in db.stations.find({}, {"area": 1})}
    chunks = [(tuple(c["min"][f] for f in key), tuple(c["max"][f] for f in key), c["shard"])
              for c in db.client.config.chunks.find({"uuid": meta["uuid"]})]
    return {s: next(shard for lo, hi, shard in chunks if lo <= p < hi) for s, p in points.items()}


def shards_touched(explain) -> list[str]:
    """Shard names appearing in an explain() output from mongos."""
    names = set()

    def visit(v):
        if isinstance(v, dict):
            if isinstance(v.get("shardName"), str):
                names.add(v["shardName"])
            if isinstance(v.get("shards"), dict):
                names.update(v["shards"])
            for c in v.values():
                visit(c)
        elif isinstance(v, list):
            for c in v:
                visit(c)
    visit(explain)
    return sorted(names)


def needed_stations(db, command: str, args) -> list[str]:
    """Stations whose observations a request reads (or writes, for F5)."""
    if command == "f1":
        return [args.station]
    if command == "trace":
        return sorted({i.split(":", 1)[0] for i in args.ids}) if args.ids else [args.station]
    if command == "stations":
        return []  # reads only the stations collection; guard() still checks the metadata
    if command == "f3":
        return db.stations.distinct("_id", {"area": {"$in": list(args.area)}})
    if command == "f5":
        return db.stations.distinct("_id")  # an import may write to any shard
    q = station_filter(getattr(args, "area", None), getattr(args, "bbox", None), getattr(args, "near", None))
    if getattr(args, "stations", None):
        q["_id"] = {"$in": list(args.stations)}
    return db.stations.distinct("_id", q)


def guard(db, command: str, args) -> dict | None:
    """None if the request can be answered completely, else an UNAVAILABLE answer."""
    if not is_cluster(db):
        return None
    try:
        health = shard_status(db)
        down = sorted(n for n, s in health.items() if not s["up"])
        if not down:
            return None
        needed = needed_stations(db, command, args)
        owners = {}
        for coll in SHARDED:
            for s, shard in station_shards(db, coll).items():
                owners.setdefault(s, set()).add(shard)
        lost = sorted(s for s in needed if owners.get(s, set()) & set(down))
        if not lost and command != "stations":
            return None
        if command == "stations" and not lost:
            db.stations.find_one()  # the metadata itself must be readable
            return None
        areas = sorted({a["area"] for a in db.stations.find({"_id": {"$in": lost}}, {"area": 1})})
        return {"service": command, "status": "UNAVAILABLE", "complete": False,
                "missing": {"shards": down, "stations": len(lost), "examples": lost[:10], "areas": areas},
                "message": "No answer: observations this request needs are on an unreachable shard."}
    except (PyMongoError, RuntimeError, StopIteration) as e:
        return {"service": command, "status": "UNAVAILABLE", "complete": False,
                "missing": {"metadata": True},
                "message": f"No answer: the cluster metadata cannot be read ({e.__class__.__name__})."}
