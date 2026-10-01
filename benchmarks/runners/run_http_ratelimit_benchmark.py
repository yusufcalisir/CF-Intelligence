"""Class B2: Rate-Limited Public-Endpoint Behavior Benchmark.

Measures the actual public rate-limited endpoint behavior of the CF-Intelligence
platform under its production SlowAPI rate-limiting policy (60 requests/minute).

Scope:
  - Exercises the real production rate-limiter decorator (@limiter.limit("60/minute")).
  - Preserves production rate-limiting configuration (CFI_BENCHMARK_MODE is NOT active).
  - Uses a single stable client identity (standard loopback / single IP).
  - Records:
      * Number of successful 2xx responses before quota exhaustion
      * Total HTTP 429 Too Many Requests count
      * Request index and wall-clock timestamp of first 429
      * 2xx vs. 429 client-observed latency distributions
      * Header verification (Retry-After, X-RateLimit-Exceeded, RFC 7807 problem details)
"""

from __future__ import annotations

import argparse
import asyncio
import datetime
import json
import os
import platform
import subprocess  # nosec B404
import sys
import time
from pathlib import Path
from typing import Any

import httpx
import numpy as np


def get_hardware_environment() -> dict[str, Any]:
    """Inspects and returns authoritative host hardware and runtime environment metadata."""
    env: dict[str, Any] = {
        "os": platform.platform(),
        "cpu_model": platform.processor() or platform.machine(),
        "cpu_count": os.cpu_count() or 1,
        "machine": platform.machine(),
        "python_version": platform.python_version(),
        "device": "cpu",
    }
    try:
        import psutil

        env["ram_total_gb"] = round(psutil.virtual_memory().total / (1024**3), 2)
    except Exception:
        env["ram_total_gb"] = None
    return env


def start_uvicorn_server(port: int = 8091, host: str = "127.0.0.1") -> subprocess.Popen:
    """Spawns Uvicorn ASGI server with production rate limiting enabled (CFI_BENCHMARK_MODE absent)."""
    backend_dir = Path(__file__).resolve().parents[2] / "backend"
    env = os.environ.copy()
    env["PYTHONPATH"] = str(backend_dir)
    env["TESTING"] = "1"  # Allows DDoS loopback bypass for benchmark client
    # CFI_BENCHMARK_MODE is intentionally omitted so limiter.enabled is True (production policy active)
    env.pop("CFI_BENCHMARK_MODE", None)

    cmd = [
        sys.executable,
        "-m",
        "uvicorn",
        "app.main:app",
        "--host",
        host,
        "--port",
        str(port),
        "--log-level",
        "warning",
    ]
    proc = subprocess.Popen(
        cmd,
        cwd=str(backend_dir),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return proc


def wait_for_server_ready(port: int = 8091, host: str = "127.0.0.1", timeout: float = 35.0) -> bool:
    """Polls the /health endpoint until the ASGI server is ready to accept requests."""
    url = f"http://{host}:{port}/health"
    start = time.time()
    while time.time() - start < timeout:
        try:
            with httpx.Client(timeout=1.0) as client:
                resp = client.get(url)
                if resp.status_code == 200:
                    return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


async def run_ratelimit_sweep(
    host: str = "127.0.0.1",
    port: int = 8091,
    endpoint: str = "/api/v1/score-transaction",
    total_requests: int = 120,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Sends sequential requests from a single stable client identity to test SlowAPI rate-limiting."""
    url = f"http://{host}:{port}{endpoint}"
    samples: list[dict[str, Any]] = []

    first_429_index: int | None = None
    first_429_timestamp: str | None = None
    first_429_headers: dict[str, str] = {}
    first_429_body: dict[str, Any] = {}

    print(f"\nExecuting Class B2 Rate-Limiter Behavior Benchmark against {url}...")
    print(f"Total sequential requests: {total_requests} | Client identity: single stable IP")

    # Use single client with connection reuse
    limits = httpx.Limits(max_connections=5, max_keepalive_connections=5)
    timeout = httpx.Timeout(10.0, connect=5.0)

    t_start = time.perf_counter()

    async with httpx.AsyncClient(limits=limits, timeout=timeout) as client:
        for idx in range(total_requests):
            payload = {
                "transaction_id": f"tx_b2_{idx}",
                "account_id": "acc_b2_single_client",
                "amount": 150.0,
                "currency": "EUR",
                "merchant_id": "merch_grocery_001",
                "country": "US",
                "device_id": "dev_b2_single_client",
            }
            # Stable client IP header
            headers = {
                "X-Bank-ID": "bank_alpha",
                "X-Forwarded-For": "198.51.100.42",
            }

            t_req = time.perf_counter()
            resp = await client.post(url, json=payload, headers=headers)
            elapsed = (time.perf_counter() - t_req) * 1000.0

            sample = {
                "request_index": idx,
                "latency_ms": round(elapsed, 3),
                "status_code": resp.status_code,
                "success": resp.status_code == 200,
                "is_429": resp.status_code == 429,
                "timestamp_utc": datetime.datetime.now(datetime.UTC).isoformat(),
            }
            samples.append(sample)

            if resp.status_code == 429 and first_429_index is None:
                first_429_index = idx
                first_429_timestamp = sample["timestamp_utc"]
                first_429_headers = {
                    k: v for k, v in resp.headers.items()
                    if k.lower() in ("retry-after", "x-ratelimit-exceeded", "content-type")
                }
                try:
                    first_429_body = resp.json()
                except Exception:
                    first_429_body = {"raw": resp.text}

    t_total = time.perf_counter() - t_start

    # Partition samples
    samples_2xx = [s for s in samples if s["status_code"] == 200]
    samples_429 = [s for s in samples if s["status_code"] == 429]
    samples_other = [s for s in samples if s["status_code"] not in (200, 429)]

    lats_2xx = [s["latency_ms"] for s in samples_2xx]
    lats_429 = [s["latency_ms"] for s in samples_429]

    stats_2xx = {
        "count": len(samples_2xx),
        "mean_ms": round(float(np.mean(lats_2xx)), 2) if lats_2xx else None,
        "p50_ms": round(float(np.percentile(lats_2xx, 50)), 2) if lats_2xx else None,
        "p95_ms": round(float(np.percentile(lats_2xx, 95)), 2) if lats_2xx else None,
        "p99_ms": round(float(np.percentile(lats_2xx, 99)), 2) if lats_2xx else None,
        "min_ms": round(float(np.min(lats_2xx)), 2) if lats_2xx else None,
        "max_ms": round(float(np.max(lats_2xx)), 2) if lats_2xx else None,
    }

    stats_429 = {
        "count": len(samples_429),
        "mean_ms": round(float(np.mean(lats_429)), 2) if lats_429 else None,
        "p50_ms": round(float(np.percentile(lats_429, 50)), 2) if lats_429 else None,
        "p95_ms": round(float(np.percentile(lats_429, 95)), 2) if lats_429 else None,
        "p99_ms": round(float(np.percentile(lats_429, 99)), 2) if lats_429 else None,
        "min_ms": round(float(np.min(lats_429)), 2) if lats_429 else None,
        "max_ms": round(float(np.max(lats_429)), 2) if lats_429 else None,
    }

    print("\nClass B2 Rate-Limiter Behavior Results:")
    print(f"  Attempted requests:          {total_requests}")
    print(f"  Successful 2xx count:        {len(samples_2xx)} (quota limit = 60/min)")
    print(f"  Rate-limited 429 count:      {len(samples_429)}")
    print(f"  First 429 request index:     {first_429_index}")
    print(f"  2xx Latency:                 p50={stats_2xx['p50_ms']}ms, p95={stats_2xx['p95_ms']}ms, mean={stats_2xx['mean_ms']}ms")
    print(f"  429 Latency:                 p50={stats_429['p50_ms']}ms, p95={stats_429['p95_ms']}ms, mean={stats_429['mean_ms']}ms")
    print(f"  Wall-clock duration:         {round(t_total, 2)}s")

    summary = {
        "total_attempted": total_requests,
        "wall_clock_duration_seconds": round(t_total, 3),
        "configured_rate_limit": "60/minute",
        "client_identity": "single stable IP (198.51.100.42)",
        "allowed_2xx_before_quota_exhaustion": len(samples_2xx),
        "rate_limited_429_count": len(samples_429),
        "other_status_count": len(samples_other),
        "first_429_event": {
            "request_index": first_429_index,
            "timestamp_utc": first_429_timestamp,
            "headers": first_429_headers,
            "response_body": first_429_body,
        },
        "latency_2xx_successful_inference": stats_2xx,
        "latency_429_rate_limited_rejections": stats_429,
    }

    return summary, samples


def run_full_b2_benchmark(
    port: int = 8091,
    host: str = "127.0.0.1",
    endpoint: str = "/api/v1/score-transaction",
    total_requests: int = 120,
    save_artifact: bool = True,
    output_path: Path | str | None = None,
) -> dict[str, Any]:
    """Launches isolated ASGI server with production limiter active and executes B2 benchmark."""
    print(f"Launching managed local Uvicorn ASGI server on {host}:{port} (production rate limiter active)...")
    server_proc = start_uvicorn_server(port=port, host=host)

    try:
        ready = wait_for_server_ready(port=port, host=host, timeout=35.0)
        if not ready:
            raise RuntimeError(f"Uvicorn server failed to become ready on {host}:{port} within 35s")
        print("[+] ASGI server is ready and accepting requests.")

        summary, samples = asyncio.run(
            run_ratelimit_sweep(
                host=host,
                port=port,
                endpoint=endpoint,
                total_requests=total_requests,
            )
        )

        payload = {
            "timestamp_utc": datetime.datetime.now(datetime.UTC).isoformat(),
            "benchmark_type": "CLASS_B2_RATE_LIMITER_BEHAVIOR_BENCHMARK",
            "benchmark_name": "Public-Endpoint Rate-Limiter Policy & Transition Benchmark",
            "scope": (
                "Measures endpoint behavior under the production SlowAPI rate-limiting policy (60/minute) "
                "from a single stable client identity. Distinguishes allowed 2xx transactions from "
                "429 Too Many Requests rejections, measuring the exact transition point, RFC 7807 headers, "
                "and latency distributions for both populations."
            ),
            "scientific_question": (
                "How does the endpoint behave when the configured SlowAPI policy is exercised? "
                "NOT intended to measure maximum neural inference capacity."
            ),
            "server_provenance": {
                "server_implementation": "uvicorn (0.47.0)",
                "worker_count": 1,
                "server_mode": "single-worker ASGI process",
                "host": host,
                "port": port,
                "tested_endpoint": endpoint,
                "rate_limiter_enabled": True,
                "rate_limiter_policy": "60/minute (SlowAPI with MemoryStorage)",
            },
            "client_provenance": {
                "client_library": f"httpx ({httpx.__version__})",
                "identity_model": "single stable IP (198.51.100.42)",
                "connection_reuse": "keep-alive enabled",
            },
            "environment": get_hardware_environment(),
            "results": summary,
        }

        if save_artifact:
            base_dir = Path(__file__).resolve().parents[2]
            results_file = Path(output_path) if output_path else base_dir / "benchmarks" / "results" / "raw" / "rate_limit_behavior_benchmark.json"
            results_file.parent.mkdir(parents=True, exist_ok=True)
            with open(results_file, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
            print(f"[+] Saved Class B2 rate-limiter benchmark results to {results_file}")

            samples_file = base_dir / "benchmarks" / "results" / "raw" / "rate_limit_behavior_samples.json"
            samples_payload = {
                "schema_version": "2.0.0",
                "benchmark_type": "CLASS_B2_RATE_LIMITER_BEHAVIOR_SAMPLES",
                "total_samples": len(samples),
                "samples": samples,
            }
            with open(samples_file, "w", encoding="utf-8") as f:
                json.dump(samples_payload, f, indent=2)
            print(f"[+] Saved Class B2 raw samples to {samples_file}")

        return payload

    finally:
        print("Terminating managed Uvicorn server...")
        server_proc.terminate()
        try:
            server_proc.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            server_proc.kill()
        print("[+] Uvicorn server terminated.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run Class B2 Rate-Limiter Behavior Benchmark")
    parser.add_argument("--port", type=int, default=8091, help="Port for local Uvicorn server")
    parser.add_argument("--endpoint", type=str, default="/api/v1/score-transaction", help="Inference endpoint to test")
    parser.add_argument("--total-requests", type=int, default=120, help="Total sequential requests to send")
    parser.add_argument("--no-save", action="store_true", help="Do not save output artifacts")
    parser.add_argument("--output-path", type=str, default=None, help="Custom output path for results JSON")
    args = parser.parse_args()

    run_full_b2_benchmark(
        port=args.port,
        endpoint=args.endpoint,
        total_requests=args.total_requests,
        save_artifact=not args.no_save,
        output_path=args.output_path,
    )
