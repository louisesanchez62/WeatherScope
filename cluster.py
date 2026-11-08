#!/usr/bin/env python3
"""Gestionnaire du cluster MongoDB local de WeatherScope (Part II).

    python cluster.py start [--shards 2]   démarre config + shards + mongos (idempotent)
    python cluster.py start-local          démarre le mongod local (port 27017) pour les comparaisons
    python cluster.py status               état de chaque processus
    python cluster.py kill NOM             arrêt brutal (shard1, shard2, shard3, config, mongos, local)
    python cluster.py stop                 arrête tout
    python cluster.py reset                arrête tout ET supprime cluster_data/

Relancer `start` ne redémarre que ce qui est tombé : sert aussi à restaurer après une panne.
Prérequis : mongod et mongos dans le PATH, pip install pymongo.
Budget de cache WiredTiger total : variable WS_CACHE_TOTAL_GB (défaut 2 Go), réparti entre les shards.
"""
import argparse
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

from pymongo import MongoClient
from pymongo.errors import OperationFailure, PyMongoError

ROOT = Path("cluster_data").resolve()
TOTAL_CACHE_GB = float(os.getenv("WS_CACHE_TOTAL_GB", "2"))
PORTS = {"local": 27017, "config": 27200, "shard1": 27301, "shard2": 27302, "shard3": 27303, "mongos": 27400}
RS = {"config": "configRS", "shard1": "shard1RS", "shard2": "shard2RS", "shard3": "shard3RS"}


def direct(port, ms=500):
    return MongoClient(host="localhost", port=port, directConnection=True, serverSelectionTimeoutMS=ms)


def is_up(name):
    try:
        direct(PORTS[name]).admin.command("ping")
        return True
    except PyMongoError:
        return False


def wait(name, timeout=60):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if is_up(name):
            return
        time.sleep(0.5)
    sys.exit(f"{name} ne répond pas : voir {ROOT / 'logs' / (name + '.log')}")


def spawn(name, cmd):
    (ROOT / "logs").mkdir(parents=True, exist_ok=True)
    log = open(ROOT / "logs" / f"{name}.log", "ab")
    if os.name == "nt":
        flags = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    else:
        flags = {"start_new_session": True}
    p = subprocess.Popen(cmd, stdout=log, stderr=log, **flags)
    (ROOT / f"{name}.pid").write_text(str(p.pid))


def mongod_cmd(name, cache_gb):
    d = ROOT / name
    d.mkdir(parents=True, exist_ok=True)
    cmd = ["mongod", "--dbpath", str(d), "--port", str(PORTS[name]), "--bind_ip", "localhost",
           "--wiredTigerCacheSizeGB", str(cache_gb)]
    if name == "config":
        cmd += ["--configsvr", "--replSet", RS[name]]
    elif name.startswith("shard"):
        cmd += ["--shardsvr", "--replSet", RS[name]]
    return cmd


def init_rs(name):
    c = direct(PORTS[name], 3000)
    cfg = {"_id": RS[name], "members": [{"_id": 0, "host": f"localhost:{PORTS[name]}"}]}
    if name == "config":
        cfg["configsvr"] = True
    try:
        c.admin.command("replSetInitiate", cfg)
    except OperationFailure as e:
        if e.code != 23:                      # 23 = déjà initialisé
            raise
    while not c.admin.command("hello").get("isWritablePrimary"):
        time.sleep(0.5)


def start(n):
    if n < 2 or n > 3:
        sys.exit("--shards doit être 2 ou 3 (ports réservés: shard1..shard3)")
    shards = [f"shard{i}" for i in range(1, n + 1)]
    cache = max(0.25, round(TOTAL_CACHE_GB / n, 2))
    for name in ["config", *shards]:
        if not is_up(name):
            spawn(name, mongod_cmd(name, 0.25 if name == "config" else cache))
    for name in ["config", *shards]:
        wait(name)
        init_rs(name)
    if not is_up("mongos"):
        spawn("mongos", ["mongos", "--configdb", f"configRS/localhost:{PORTS['config']}",
                         "--port", str(PORTS["mongos"]), "--bind_ip", "localhost"])
    wait("mongos")
    admin = MongoClient(port=PORTS["mongos"]).admin
    for name in shards:
        admin.command("addShard", f"{RS[name]}/localhost:{PORTS[name]}")
    print(f"cluster prêt : {n} shards, {cache} Go de cache chacun, mongos sur {PORTS['mongos']}")


def kill(name):
    if name not in PORTS:
        sys.exit(f"instance inconnue: {name}")
    f = ROOT / f"{name}.pid"
    if not f.exists():
        return
    pid = int(f.read_text())
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
        else:
            os.kill(pid, signal.SIGKILL)      # crash brutal : plus réaliste qu'un arrêt propre
    except ProcessLookupError:
        pass
    f.unlink(missing_ok=True)


if __name__ == "__main__":
    a = argparse.ArgumentParser()
    a.add_argument("cmd")
    a.add_argument("name", nargs="?")
    a.add_argument("--shards", type=int, default=2)
    x = a.parse_args()
    if x.cmd == "start":
        start(x.shards)
    elif x.cmd == "start-local":
        if not is_up("local"):
            spawn("local", mongod_cmd("local", TOTAL_CACHE_GB))
        wait("local")
        print("mongod local prêt sur 27017")
    elif x.cmd == "kill":
        kill(x.name)
    elif x.cmd == "stop":
        for n in ["mongos", "shard3", "shard2", "shard1", "config", "local"]:
            kill(n)
    elif x.cmd == "status":
        for n in PORTS:
            print(f"{n:7s} {PORTS[n]}  {'UP' if is_up(n) else 'down'}")
    elif x.cmd == "reset":
        for n in PORTS:
            kill(n)
        time.sleep(2)
        shutil.rmtree(ROOT, ignore_errors=True)
    else:
        sys.exit(__doc__)
