"""Database connection shared by every service.

The same code talks to the Part I server (mongod) and to the Part II router
(mongos): only the URI changes.
"""
import os

from pymongo import MongoClient

DEFAULT_URI = "mongodb://localhost:27017"
DEFAULT_DB = "weatherscope"


def connect(uri: str | None = None, db: str | None = None):
    uri = uri or os.environ.get("WEATHERSCOPE_URI", DEFAULT_URI)
    name = db or os.environ.get("WEATHERSCOPE_DB", DEFAULT_DB)
    client = MongoClient(uri, serverSelectionTimeoutMS=5000, tz_aware=False)
    return client[name]
