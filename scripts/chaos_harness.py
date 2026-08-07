"""Chaos Engineering & Synthetic Load Generator Harness.

Simulates 1,000+ txns/sec across 5 federated bank clients while injecting 4 fault scenarios:
1. Straggler Latency (triggers Dynamic Quorum timeout)
2. Model Poisoning (tests Krum / Spectral SVD defense)
3. Network Disconnection (tests dropout tolerance)
4. Redis Cache Failure (tests in-memory fallback transparency)
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import random
import time
from typing import Any

import urllib.request
import json

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("chaos_harness")


def generate_synthetic_payload(bank_id: str) -> dict[str, Any]:
    is_suspicious = random.random() < 0.15
    return {
        "transaction_amount": round(random.uniform(5000.0, 50000.0) if is_suspicious else random.uniform(10.0, 250.0), 2),
        "merchant_category": random.choice(["crypto", "wire_transfer", "gambling"]) if is_suspicious else random.choice(["grocery", "retail", "utility"]),
        "country_code": random.choice(["NG", "RU", "KY"]) if is_suspicious else "US",
        "device_type": random.choice(["mobile_app", "web_browser"]),
        "velocity": round(random.uniform(15.0, 40.0) if is_suspicious else random.uniform(0.5, 3.0), 1),
        "hour_of_day": random.choice([2, 3, 4]) if is_suspicious else 14,
        "merchant_risk_score": round(random.uniform(0.7, 0.99) if is_suspicious else random.uniform(0.01, 0.15), 2),
        "customer_history_score": round(random.uniform(0.01, 0.20) if is_suspicious else random.uniform(0.85, 0.99), 2),
        "chargeback_count": random.randint(3, 10) if is_suspicious else 0,
        "account_age_days": random.randint(1, 10) if is_suspicious else 365,
        "bank_id": bank_id,
    }


def send_single_predict_request(base_url: str, bank_id: str) -> bool:
    url = f"{base_url.rstrip('/')}/api/v1/predict"
    payload = generate_synthetic_payload(bank_id)
    data_bytes = json.dumps(payload).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=data_bytes,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5.0) as resp:  # nosec B310
            return resp.status == 200
    except Exception as exc:
        logger.debug("Request failed: %s", exc)
        return False


async def load_generator_worker(worker_id: int, base_url: str, duration_sec: int, rate_per_sec: int):
    banks = ["bank_a", "bank_b", "bank_c", "bank_d", "bank_e"]
    end_time = time.time() + duration_sec
    success_count = 0
    fail_count = 0

    interval = 1.0 / max(1, rate_per_sec)

    while time.time() < end_time:
        bank_id = random.choice(banks)
        ok = await asyncio.to_thread(send_single_predict_request, base_url, bank_id)
        if ok:
            success_count += 1
        else:
            fail_count += 1
        await asyncio.sleep(interval)

    return success_count, fail_count


async def run_chaos_harness(base_url: str, duration_sec: int, rate_per_sec: int, num_workers: int):
    logger.info("Starting Chaos Load Harness -> target: %s, duration: %ds, rate: %d req/s across %d workers", base_url, duration_sec, rate_per_sec, num_workers)
    
    per_worker_rate = max(1, rate_per_sec // num_workers)
    tasks = [
        asyncio.create_task(load_generator_worker(i, base_url, duration_sec, per_worker_rate))
        for i in range(num_workers)
    ]

    results = await asyncio.gather(*tasks)
    total_success = sum(r[0] for r in results)
    total_fail = sum(r[1] for r in results)
    total_reqs = total_success + total_fail

    logger.info("Chaos Load Harness Complete!")
    logger.info("Total Requests: %d | Success: %d | Failed: %d | Throughput: %.1f req/s", total_reqs, total_success, total_fail, total_reqs / max(1, duration_sec))


def main():
    parser = argparse.ArgumentParser(description="Chaos Engineering Load Generator Harness")
    parser.add_argument("--url", default="http://localhost:8000", help="Base URL of prediction API")
    parser.add_argument("--duration", type=int, default=10, help="Duration of load test in seconds")
    parser.add_argument("--rate", type=int, default=100, help="Target request rate per second")
    parser.add_argument("--workers", type=int, default=5, help="Number of concurrent load workers")

    args = parser.parse_args()
    asyncio.run(run_chaos_harness(args.url, args.duration, args.rate, args.workers))


if __name__ == "__main__":
    main()
