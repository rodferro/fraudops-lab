"""Concurrent HTTP load test for the running FraudOps API; uses only stdlib."""

from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor


PAYLOAD = json.dumps(
    {
        "amount": 250.0,
        "account_age_days": 120,
        "transactions_last_24h": 5,
        "distance_from_home_km": 150.0,
        "device_age_days": 30,
        "country_mismatch": 1,
        "card_present": 0,
        "channel_code": 0,
        "merchant_category_code": 2,
    }
).encode("utf-8")


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int(fraction * len(ordered) + 0.999999) - 1))
    return round(ordered[index], 3)


def request_once(base_url: str) -> tuple[float, str | None]:
    request = urllib.request.Request(
        f"{base_url}/api/v1/predict",
        data=PAYLOAD,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            response.read()
            elapsed_ms = (time.perf_counter() - started) * 1000
            if response.status != 200:
                return elapsed_ms, f"HTTP {response.status}"
            return elapsed_ms, None
    except (urllib.error.URLError, TimeoutError) as exception:
        return (time.perf_counter() - started) * 1000, type(exception).__name__


def read_heap_used(base_url: str) -> int | str:
    request = urllib.request.Request(
        f"{base_url}/actuator/metrics/jvm.memory.used?tag=area:heap"
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            metrics = json.load(response)
        return int(metrics["measurements"][0]["value"])
    except (urllib.error.URLError, KeyError, IndexError, ValueError):
        return "unavailable"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://localhost:8080")
    parser.add_argument("--requests", type=int, default=2000)
    parser.add_argument("--concurrency", type=int, default=16)
    parser.add_argument("--warmup-requests", type=int, default=200)
    args = parser.parse_args()
    if args.requests < 1 or args.concurrency < 1 or args.warmup_requests < 0:
        parser.error("--requests and --concurrency must be positive; --warmup-requests cannot be negative")

    with ThreadPoolExecutor(max_workers=args.concurrency) as executor:
        warmup = list(executor.map(lambda _: request_once(args.base_url), range(args.warmup_requests)))
        warmup_errors = [error for _, error in warmup if error is not None]
        if warmup_errors:
            raise RuntimeError(f"Warmup failed: {warmup_errors[:5]}")
        started = time.perf_counter()
        results = list(executor.map(lambda _: request_once(args.base_url), range(args.requests)))
        elapsed = time.perf_counter() - started
    latencies = [latency for latency, _ in results]
    errors = [error for _, error in results if error is not None]
    output = {
        "requests": args.requests,
        "concurrency": args.concurrency,
        "warmup_requests": args.warmup_requests,
        "duration_seconds": round(elapsed, 3),
        "throughput_requests_per_second": round(args.requests / elapsed, 2),
        "p50_latency_ms": percentile(latencies, 0.50),
        "p95_latency_ms": percentile(latencies, 0.95),
        "p99_latency_ms": percentile(latencies, 0.99),
        "error_count": len(errors),
        "error_rate": round(len(errors) / args.requests, 6),
        "jvm_heap_used_bytes": read_heap_used(args.base_url),
        "error_samples": errors[:5],
    }
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()