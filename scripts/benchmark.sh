#!/usr/bin/env bash
# Reproduce the numbers in docs/CAPACITY.md.
#
#   scripts/benchmark.sh [workers]        # default 1; needs ApacheBench (ab)
#
# Starts StockLens on demo data with the rate limits raised (they would
# otherwise, correctly, throttle a load test), warms every worker, then
# measures cached and cold paths. Nothing leaves the machine.
set -euo pipefail
WORKERS="${1:-1}"
PORT=18080
DB="$(mktemp -d)/bench.db"
export STOCKLENS_DATABASE_URL="sqlite:///$DB" STOCKLENS_LOG_LEVEL=WARNING \
       STOCKLENS_RATE_LIMIT_PER_MINUTE=100000000 STOCKLENS_COMPUTE_RATE_LIMIT_PER_MINUTE=100000000 \
       STOCKLENS_LEDGER_DAILY_LIMIT=100000000
uvicorn --factory stocklens.app:app_factory --port "$PORT" --workers "$WORKERS" \
        --no-access-log --log-level warning &
SERVER=$!
trap 'kill $SERVER 2>/dev/null' EXIT
for _ in $(seq 1 60); do curl -fs "localhost:$PORT/healthz" >/dev/null && break; sleep 0.5; done
curl -fs "localhost:$PORT/healthz" >/dev/null || { echo "server did not start on :$PORT" >&2; exit 1; }

ab -q -k -c 16 -n 400 "http://127.0.0.1:$PORT/api/v1/symbols/MSFT/report" >/dev/null   # warm
summary() { grep -E "Requests per second|Failed requests|  50%|  99%" | tr -s ' ' | paste -sd' ' -; }
echo "workers=$WORKERS"
echo "healthz           $(ab -q -k -c 64 -n 20000 "http://127.0.0.1:$PORT/healthz" | summary)"
echo "report (cached)   $(ab -q -k -c 64 -n 20000 "http://127.0.0.1:$PORT/api/v1/symbols/MSFT/report" | summary)"
echo "portfolio (cached) $(ab -q -k -c 64 -n 10000 "http://127.0.0.1:$PORT/api/v1/portfolio?symbols=AAPL,MSFT,NVDA,JPM" | summary)"
python scripts/bench_cold.py --url "http://127.0.0.1:$PORT" --requests $((WORKERS * 40)) \
       --concurrency $((WORKERS * 2))
