"""Real load test - no mocks, actual ASGI calls, real measured latency."""
from __future__ import annotations

import asyncio
import json
import random
import statistics
import sys
import time
import uuid
from pathlib import Path

# Ensure backend/ is on the import path
_BACKEND = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(_BACKEND))

import httpx

BANK_IDS = ["bank_alpha", "bank_beta", "bank_gamma"]
MERCHANT_IDS = [
    "merchant_grocery",
    "merchant_crypto_exchange",
    "merchant_wire_transfer",
    "merchant_gambling",
    "merchant_electronics",
    "merchant_jewelry",
]
CURRENCIES = ["EUR", "USD", "GBP"]
COUNTRIES = ["DE", "FR", "US", "GB", "TR", "NL"]


def _score_payload() -> dict:
    return {
        "transaction_id": f"tx_{uuid.uuid4().hex[:12]}",
        "account_id": f"acc_{random.randint(1, 10000)}",
        "amount": round(random.uniform(5.0, 75000.0), 2),
        "currency": random.choice(CURRENCIES),
        "merchant_id": random.choice(MERCHANT_IDS),
        "country": random.choice(COUNTRIES),
        "device_id": f"dev_{uuid.uuid4().hex[:8]}",
    }


def _predict_payload() -> dict:
    return {
        "transaction_amount": round(random.uniform(5.0, 50000.0), 2),
        "merchant_category": random.choice(
            ["grocery", "electronics", "crypto", "travel", "dining", "wire_transfer"]
        ),
        "country_code": random.choice(COUNTRIES),
        "device_type": random.choice(["web_browser", "mobile_app", "pos_terminal"]),
        "velocity": round(random.uniform(0.5, 10.0), 1),
        "hour_of_day": random.randint(0, 23),
        "merchant_risk_score": round(random.uniform(0.01, 0.50), 2),
        "customer_history_score": round(random.uniform(0.70, 0.99), 2),
        "chargeback_count": random.randint(0, 2),
        "account_age_days": random.randint(30, 1500),
        "bank_id": random.choice(BANK_IDS),
    }


async def run_realtime_benchmark(
    n_requests: int = 500,
    concurrency: int = 20,
    sla_ms: float = 100.0,
) -> dict:
    from app.main import app

    transport = httpx.ASGITransport(app=app)
    latencies_score: list[float] = []
    latencies_predict: list[float] = []
    latencies_jit: list[float] = []
    errors = 0

    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver", timeout=15.0
    ) as client:
        # ── Warm-up ────────────────────────────────────────────────
        print("[WARM-UP] 15 requests to prime JIT model and middleware...", flush=True)
        for bank_id in BANK_IDS:
            for _ in range(5):
                try:
                    await client.post(
                        "/api/v1/transactions/score",
                        json=_score_payload(),
                        headers={"X-Tenant-ID": bank_id, "X-Bank-ID": bank_id},
                    )
                    await client.post(
                        "/api/v1/predict",
                        json=_predict_payload(),
                        headers={"X-Tenant-ID": bank_id, "X-Bank-ID": bank_id},
                    )
                except Exception:
                    pass
        print("[WARM-UP] Complete.\n", flush=True)

        sem = asyncio.Semaphore(concurrency)

        async def one_request(i: int, endpoint: str, payload_fn):
            nonlocal errors
            bank_id = random.choice(BANK_IDS)
            async with sem:
                t0 = time.perf_counter()
                try:
                    resp = await client.post(
                        endpoint,
                        json=payload_fn(),
                        headers={
                            "X-Tenant-ID": bank_id,
                            "X-Bank-ID": bank_id,
                            "X-Forwarded-For": f"10.{i % 256}.{(i // 256) % 256}.1",
                        },
                    )
                    elapsed = (time.perf_counter() - t0) * 1000.0
                    if resp.status_code in (200, 429):
                        return elapsed
                    errors += 1
                    return (time.perf_counter() - t0) * 1000.0
                except Exception:
                    errors += 1
                    return None

        # ── Phase 1: /api/v1/transactions/score  ────────────────────
        print(f"[PHASE 1/3] /api/v1/transactions/score  — {n_requests} req @ {concurrency} concurrency", flush=True)
        t0 = time.perf_counter()
        results = await asyncio.gather(
            *[one_request(i, "/api/v1/transactions/score", _score_payload) for i in range(n_requests)]
        )
        dur1 = time.perf_counter() - t0
        latencies_score = [r for r in results if r is not None]
        print(f"  Done in {dur1:.2f}s  ({n_requests/dur1:.0f} req/s)", flush=True)

        # ── Phase 2: /api/v1/predict  ────────────────────────────────
        print(f"[PHASE 2/3] /api/v1/predict              — {n_requests} req @ {concurrency} concurrency", flush=True)
        t0 = time.perf_counter()
        results2 = await asyncio.gather(
            *[one_request(i, "/api/v1/predict", _predict_payload) for i in range(n_requests)]
        )
        dur2 = time.perf_counter() - t0
        latencies_predict = [r for r in results2 if r is not None]
        print(f"  Done in {dur2:.2f}s  ({n_requests/dur2:.0f} req/s)", flush=True)

        # ── Phase 3: /v1/inference/score  ────────────────────────────
        def _jit_payload():
            return {
                "transaction_id": f"tx_{uuid.uuid4().hex[:12]}",
                "amount": round(random.uniform(10.0, 100000.0), 2),
                "currency": random.choice(CURRENCIES),
                "source_account": f"acc_{random.randint(1, 9999)}",
                "target_account": f"acc_{random.randint(10000, 99999)}",
                "merchant_category": random.choice(
                    ["general_retail", "crypto_exchange", "gambling", "grocery"]
                ),
                "velocity_1h": random.randint(1, 15),
            }

        print(f"[PHASE 3/3] /v1/inference/score (JIT)    — {n_requests} req @ {concurrency} concurrency", flush=True)
        t0 = time.perf_counter()
        results3 = await asyncio.gather(
            *[one_request(i, "/v1/inference/score", _jit_payload) for i in range(n_requests)]
        )
        dur3 = time.perf_counter() - t0
        latencies_jit = [r for r in results3 if r is not None]
        print(f"  Done in {dur3:.2f}s  ({n_requests/dur3:.0f} req/s)", flush=True)

    def stats(lats: list[float], dur: float) -> dict:
        if not lats:
            return {}
        s = sorted(lats)
        n = len(s)
        pct = lambda p: round(s[min(int(p * n), n - 1)], 2)  # noqa: E731
        within_sla = sum(1 for lat in lats if lat <= sla_ms)
        return {
            "n_successful": n,
            "throughput_rps": round(n_requests / dur, 1),
            "min_ms": pct(0),
            "p50_ms": pct(0.50),
            "mean_ms": round(statistics.mean(lats), 2),
            "p90_ms": pct(0.90),
            "p95_ms": pct(0.95),
            "p99_ms": pct(0.99),
            "max_ms": pct(1.0),
            "stdev_ms": round(statistics.stdev(lats), 2) if len(lats) > 1 else 0.0,
            "sla_compliance_pct": round(within_sla / n * 100, 2),
            "sla_threshold_ms": sla_ms,
            "sla_pass": pct(0.99) <= sla_ms,
        }

    report = {
        "run_ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "config": {
            "n_requests_per_endpoint": n_requests,
            "concurrency": concurrency,
            "sla_threshold_ms": sla_ms,
            "total_errors": errors,
        },
        "endpoints": {
            "score_transaction (/api/v1/transactions/score)": stats(latencies_score, dur1),
            "predict_transaction (/api/v1/predict)": stats(latencies_predict, dur2),
            "jit_inference (/v1/inference/score)": stats(latencies_jit, dur3),
        },
    }
    return report


def print_report(report: dict) -> None:
    # Force UTF-8 output on Windows terminals
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    def _pr(line: str) -> None:
        try:
            print(line, flush=True)
        except UnicodeEncodeError:
            print(line.encode("ascii", "replace").decode("ascii"), flush=True)

    _pr("\n" + "=" * 80)
    _pr("  REAL-TIME INFERENCE BENCHMARK - LIVE RESULTS (no mocks)")
    _pr(f"  Run: {report['run_ts']}  |  Concurrency: {report['config']['concurrency']}")
    _pr("=" * 80)

    for ep, s in report["endpoints"].items():
        if not s:
            continue
        badge = "[PASS]" if s["sla_pass"] else "[FAIL]"
        _pr(f"\n  {badge} {ep}")
        _pr(f"    Throughput : {s['throughput_rps']:>8.1f} req/s")
        _pr(f"    min        : {s['min_ms']:>8.2f} ms")
        _pr(f"    p50        : {s['p50_ms']:>8.2f} ms")
        _pr(f"    p90        : {s['p90_ms']:>8.2f} ms")
        _pr(f"    p95        : {s['p95_ms']:>8.2f} ms")
        _pr(f"    p99        : {s['p99_ms']:>8.2f} ms  <- SLA boundary")
        _pr(f"    max        : {s['max_ms']:>8.2f} ms")
        _pr(f"    mean+-stdev: {s['mean_ms']:.2f} +- {s['stdev_ms']:.2f} ms")
        _pr(f"    SLA<{s['sla_threshold_ms']:.0f}ms   : {s['sla_compliance_pct']:.1f}%")

    _pr("\n" + "=" * 80)
    total_reqs = report["config"]["n_requests_per_endpoint"] * 3
    _pr(f"  Total requests fired : {total_reqs}  |  Errors: {report['config']['total_errors']}")
    _pr("=" * 80 + "\n")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Real load test - no mocks, actual ASGI calls, real measured latency."
    )
    parser.add_argument("--requests", type=int, default=500)
    parser.add_argument("--concurrency", type=int, default=20)
    parser.add_argument("--sla", type=float, default=100.0)
    parser.add_argument("--output", type=str, default="reports/realtime_benchmark.json")
    args = parser.parse_args()

    report = asyncio.run(
        run_realtime_benchmark(
            n_requests=args.requests,
            concurrency=args.concurrency,
            sla_ms=args.sla,
        )
    )

    # Save JSON FIRST — before any print that might fail on encoding
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")

    print_report(report)
    print(f"[+] JSON results saved to {out}", flush=True)
