#!/usr/bin/env bash
# E2: load the full archive into both cluster profiles, the same way as the
# local server (F5 through mongos, 1961-2020 then 2021-2025, then the read
# model), timing each step. The local server is stopped first so the cluster
# has the machine to itself. Logs to results/e2/load_cluster.log; the F5
# timings also go to results/ingest/ (labels initial-<profile> and
# f5-2021-2025-<profile>).
#
#   bash scripts/experiments/e2_load_cluster.sh
set -euo pipefail
cd "$(dirname "$0")/../.."
mkdir -p results/e2
LOG=results/e2/load_cluster.log
exec > >(tee -a "$LOG") 2>&1

echo "== $(date -u +%FT%TZ) stop local server, start cluster (2 shards, 3 GB total)"
scripts/local_server.sh stop || true
python3 cluster.py start --shards 2

for P in hashed zoned; do
  DB=weatherscope; [ "$P" = zoned ] && DB=weatherscope_zoned
  echo "== $(date -u +%FT%TZ) $P: setup"
  python3 -m scripts.distributed.setup_db "$P" --reset
  echo "== $(date -u +%FT%TZ) $P: F5 1961-2020"
  python3 -m weatherscope f5 --profile "$P" --label "initial-$P" 2>/dev/null
  echo "== $(date -u +%FT%TZ) $P: F5 2021-2025"
  python3 -m weatherscope f5 --profile "$P" --label "f5-2021-2025-$P" --from 2021-01-01 --to 2025-12-31 2>/dev/null
  echo "== $(date -u +%FT%TZ) $P: read model"
  /usr/bin/time -f "read model built in %e s" python3 scripts/build_read_model.py --uri mongodb://localhost:27400 --db "$DB"
done
echo "== $(date -u +%FT%TZ) done"
