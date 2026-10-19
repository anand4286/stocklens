"""Cold-path throughput: every request is a symbol no worker has cached yet.

    python scripts/bench_cold.py --url http://127.0.0.1:8080 --requests 300 --concurrency 16

Run against a server started with the rate limits raised (see benchmark.sh);
demo mode accepts any valid ticker, so each request computes a fresh report.
"""

from __future__ import annotations

import argparse
import itertools
import string
import time
from concurrent.futures import ThreadPoolExecutor

import httpx


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--url", default="http://127.0.0.1:8080")
    parser.add_argument("--requests", type=int, default=200)
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument("--prefix", default="X", help="first letter of the generated tickers")
    args = parser.parse_args()

    tickers = [args.prefix + "".join(p) for p in itertools.islice(
        itertools.product(string.ascii_uppercase, repeat=3), args.requests)]
    client = httpx.Client(base_url=args.url, timeout=60)
    latencies: list[float] = []

    def hit(symbol: str) -> int:
        start = time.perf_counter()
        status = client.get(f"/api/v1/symbols/{symbol}/report").status_code
        latencies.append(time.perf_counter() - start)
        return status

    began = time.perf_counter()
    with ThreadPoolExecutor(args.concurrency) as pool:
        statuses = list(pool.map(hit, tickers))
    elapsed = time.perf_counter() - began
    latencies.sort()
    print(f"cold reports: {len(tickers) / elapsed:.1f} req/s · "
          f"p50 {latencies[len(latencies) // 2] * 1000:.0f} ms · "
          f"p99 {latencies[int(len(latencies) * 0.99)] * 1000:.0f} ms · "
          f"non-200: {sum(s != 200 for s in statuses)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
