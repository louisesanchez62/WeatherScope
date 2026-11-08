#!/usr/bin/env bash
# Part I: one local MongoDB server for WeatherScope.
# Data in data/db/local, log in data/db/local.log, port 27017 (override with PORT=...).
#
# The WiredTiger cache is capped at CACHE_GB (default 3 GB). The machine has
# 7.7 GB of RAM; Part II splits the same 3 GB total between its shards so that
# local and distributed runs are compared with the same memory budget.
#
#   scripts/local_server.sh start | stop | status
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DBPATH="$ROOT/data/db/local"
LOG="$ROOT/data/db/local.log"
PORT="${PORT:-27017}"
CACHE_GB="${CACHE_GB:-3}"

case "${1:-}" in
  start)
    mkdir -p "$DBPATH"
    mongod --dbpath "$DBPATH" --port "$PORT" --bind_ip 127.0.0.1 --fork --logpath "$LOG" --logappend \
           --wiredTigerCacheSizeGB "$CACHE_GB"
    echo "mongod running on mongodb://127.0.0.1:$PORT (data: $DBPATH, cache: ${CACHE_GB} GB)"
    ;;
  stop)
    mongosh --quiet --port "$PORT" --eval 'db.getSiblingDB("admin").shutdownServer()' >/dev/null 2>&1 || true
    echo "mongod on port $PORT stopped"
    ;;
  status)
    if mongosh --quiet --port "$PORT" --eval 'db.runCommand({ping: 1}).ok' >/dev/null 2>&1; then
      echo "running on port $PORT"
    else
      echo "not running on port $PORT"
      exit 1
    fi
    ;;
  *)
    echo "usage: $0 start|stop|status" >&2
    exit 2
    ;;
esac
