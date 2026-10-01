"""Class B1: Local HTTP Successful-Inference Capacity Benchmark.

Measures the actual request path through a live running ASGI HTTP server (Uvicorn)
over local TCP network sockets (127.0.0.1:<port>) with rate-limiting explicitly isolated.

Scientific Question:
  How does the actual HTTP scoring service behave under concurrent load
  when requests are allowed to reach and execute the inference/scoring path?

Primary Population:
  HTTP 2xx successful inference transactions. Non-2xx responses are recorded
  and reported separately, and are NEVER mixed into headline inference latency percentiles.

Methodology & Invariants:
  - Benchmark Rate-Limiting Isolation: Server is launched with CFI_BENCHMARK_MODE=1,
    which disables the SlowAPI rate limiter on the isolated benchmark server instance.
  - No Client IP Spoofing: Workers use stable, clean identity headers. Rate limiting is
    controlled at benchmark configuration level, not via thousands of synthetic IPs.
  - Equivalent Starting Conditions & State Reset: Deterministic state reset (/api/v1/benchmark/reset)
    clears rate limiter storage, tenant metering, and triggers garbage collection before each tier.
  - Server Lifecycle: Clean Uvicorn subprocess per repetition with deterministic state resets.
  - Warm-Up Isolation: 10 warmup requests executed during server initialization, followed
    immediately by a deterministic state reset before any measured traffic begins.
  - Structured Sample Schema: Each request records latency_ms, status_code, success, timeout,
    exception, worker_id, repetition, and concurrency.
  - Separate Throughput Metrics:
      * successful_inference_rps = successful 2xx / wall-clock duration
      * attempted_request_rps = total attempted / wall-clock duration
  - Concurrency Sweep: C in [1, 10, 50, 100, 250, 500] with 3 independent repetitions.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
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


def start_uvicorn_server(
    port: int = 8089,
    host: str = "127.0.0.1",
    benchmark_mode: bool = True,
) -> subprocess.Popen:
    """Spawns Uvicorn ASGI server as a managed local subprocess."""
    backend_dir = Path(__file__).resolve().parents[2] / "backend"
    env = os.environ.copy()
    env["PYTHONPATH"] = str(backend_dir)
    env["TESTING"] = "1"  # Allows DDoS loopback bypass for benchmark clients
    if benchmark_mode:
        env["CFI_BENCHMARK_MODE"] = "1"  # Isolates inference capacity from SlowAPI rate limiting

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


def wait_for_server_ready(port: int = 8089, host: str = "127.0.0.1", timeout: float = 35.0) -> bool:
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


def stop_uvicorn_server(proc: subprocess.Popen) -> None:
    """Cleanly terminates the Uvicorn server subprocess."""
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=5.0)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5.0)


def reset_server_benchmark_state(port: int = 8089, host: str = "127.0.0.1") -> bool:
    """Calls the internal benchmark state reset endpoint to reset limiter storage and tenant quotas."""
    url = f"http://{host}:{port}/api/v1/benchmark/reset"
    try:
        with httpx.Client(timeout=5.0) as client:
            resp = client.post(url)
            return resp.status_code == 200
    except Exception:
        return False


async def run_single_concurrency_tier(
    host: str,
    port: int,
    endpoint: str,
    concurrency: int,
    target_requests: int,
    repetition: int,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Executes a single concurrency tier with closed-loop workers and structured sample logging."""
    url = f"http://{host}:{port}{endpoint}"
    c = concurrency
    rpw = max(2, target_requests // c)

    # Ensure deterministic state reset immediately prior to tier execution
    reset_ok = reset_server_benchmark_state(port=port, host=host)
    if not reset_ok:
        print(f"    [!] Warning: Benchmark reset returned non-200 prior to C={c}")

    # Configure client pool sized to requested concurrency tier
    limits = httpx.Limits(
        max_connections=c + 50,
        max_keepalive_connections=c + 50,
        keepalive_expiry=30.0,
    )
    timeout = httpx.Timeout(
        timeout=60.0,
        connect=10.0,
        read=60.0,
        write=10.0,
        pool=30.0,
    )

    tier_samples: list[dict[str, Any]] = []

    async with httpx.AsyncClient(limits=limits, timeout=timeout) as client:
        async def worker(worker_id: int) -> list[dict[str, Any]]:
            w_samples: list[dict[str, Any]] = []
            for i in range(rpw):
                t_payload = {
                    "transaction_id": f"tx_b1_{repetition}_{c}_{worker_id}_{i}",
                    "account_id": f"acc_{worker_id}",
                    "amount": 250.0,
                    "currency": "EUR",
                    "merchant_id": "merch_grocery_001",
                    "country": "US",
                    "device_id": f"dev_{worker_id}",
                }
                t_headers = {
                    "X-Bank-ID": "bank_alpha",
                    "X-Tenant-ID": "bank_alpha",
                }
                t_req = time.perf_counter()
                try:
                    resp = await client.post(url, json=t_payload, headers=t_headers)
                    elapsed = (time.perf_counter() - t_req) * 1000.0
                    w_samples.append({
                        "latency_ms": round(elapsed, 3),
                        "status_code": resp.status_code,
                        "success": 200 <= resp.status_code < 300,
                        "timeout": False,
                        "exception": None,
                        "worker_id": worker_id,
                        "repetition": repetition,
                        "concurrency": c,
                    })
                except httpx.TimeoutException:
                    elapsed = (time.perf_counter() - t_req) * 1000.0
                    w_samples.append({
                        "latency_ms": round(elapsed, 3),
                        "status_code": None,
                        "success": False,
                        "timeout": True,
                        "exception": "TimeoutException",
                        "worker_id": worker_id,
                        "repetition": repetition,
                        "concurrency": c,
                    })
                except Exception as exc:
                    elapsed = (time.perf_counter() - t_req) * 1000.0
                    w_samples.append({
                        "latency_ms": round(elapsed, 3),
                        "status_code": None,
                        "success": False,
                        "timeout": False,
                        "exception": type(exc).__name__,
                        "worker_id": worker_id,
                        "repetition": repetition,
                        "concurrency": c,
                    })
            return w_samples

        t_start = time.perf_counter()
        tasks = [worker(w_id) for w_id in range(c)]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        t_total = time.perf_counter() - t_start

    for r in results:
        if isinstance(r, list):
            tier_samples.extend(r)
        else:
            # Task-level exception
            for _ in range(rpw):
                tier_samples.append({
                    "latency_ms": 0.0,
                    "status_code": None,
                    "success": False,
                    "timeout": False,
                    "exception": str(r),
                    "worker_id": -1,
                    "repetition": repetition,
                    "concurrency": c,
                })

    # Partition populations strictly by status
    samples_2xx = [s for s in tier_samples if s["success"]]
    samples_4xx = [s for s in tier_samples if s["status_code"] is not None and 400 <= s["status_code"] < 500]
    samples_5xx = [s for s in tier_samples if s["status_code"] is not None and 500 <= s["status_code"] < 600]
    timeouts = [s for s in tier_samples if s["timeout"]]
    exceptions = [s for s in tier_samples if s["exception"] is not None and not s["timeout"]]

    stat_2xx = len(samples_2xx)
    stat_4xx = len(samples_4xx)
    stat_5xx = len(samples_5xx)
    num_to = len(timeouts)
    num_exc = len(exceptions)
    attempted = len(tier_samples)
    failed = stat_4xx + stat_5xx + num_to + num_exc
    success_rate = stat_2xx / max(1, attempted)

    # Primary B1 latency statistics computed EXCLUSIVELY on successful HTTP 2xx transactions
    lats_2xx = [s["latency_ms"] for s in samples_2xx]
    p50_2xx = float(np.percentile(lats_2xx, 50)) if lats_2xx else 0.0
    p95_2xx = float(np.percentile(lats_2xx, 95)) if lats_2xx else 0.0
    p99_2xx = float(np.percentile(lats_2xx, 99)) if lats_2xx else 0.0
    mean_2xx = float(np.mean(lats_2xx)) if lats_2xx else 0.0
    max_2xx = float(np.max(lats_2xx)) if lats_2xx else 0.0

    # Primary throughput: successful HTTP 2xx transactions / total wall clock
    successful_inference_rps = stat_2xx / (t_total + 1e-8)
    attempted_request_rps = attempted / (t_total + 1e-8)

    tier_metrics = {
        "concurrency": c,
        "repetition": repetition,
        "attempted": attempted,
        "status_2xx": stat_2xx,
        "status_4xx": stat_4xx,
        "status_5xx": stat_5xx,
        "timeouts": num_to,
        "exceptions": num_exc,
        "failed": failed,
        "success_rate": round(success_rate, 4),
        "4xx_rate": round(stat_4xx / max(1, attempted), 4),
        "5xx_rate": round(stat_5xx / max(1, attempted), 4),
        "timeout_rate": round(num_to / max(1, attempted), 4),
        "exception_rate": round(num_exc / max(1, attempted), 4),
        "duration_seconds": round(t_total, 3),
        "successful_inference_rps": round(successful_inference_rps, 1),
        "attempted_request_rps": round(attempted_request_rps, 1),
        "latency_2xx_p50_ms": round(p50_2xx, 2),
        "latency_2xx_p95_ms": round(p95_2xx, 2),
        "latency_2xx_p99_ms": round(p99_2xx, 2),
        "latency_2xx_mean_ms": round(mean_2xx, 2),
        "latency_2xx_max_ms": round(max_2xx, 2),
    }

    # Print Re-evaluation Result Table Row (Section 31)
    print(
        f"| {c:>3} | {repetition:>3} | {attempted:>9} | {stat_2xx:>7} | {stat_4xx:>5} | {stat_5xx:>5} | "
        f"{num_to:>7} | {num_exc:>9} | {success_rate:>8.1%} | {mean_2xx:>8.2f} | {p50_2xx:>7.2f} | "
        f"{p95_2xx:>7.2f} | {p99_2xx:>7.2f} | {max_2xx:>7.2f} | {successful_inference_rps:>14.1f} |"
    )

    return tier_metrics, tier_samples


async def run_http_benchmark_sweep(
    host: str = "127.0.0.1",
    port: int = 8089,
    endpoint: str = "/api/v1/score-transaction",
    concurrency_levels: list[int] | None = None,
    target_requests_per_tier: int = 1000,
    repetitions: int = 3,
) -> tuple[list[dict[str, Any]], dict[str, Any], list[dict[str, Any]]]:
    """Coordinates multi-repetition benchmark sweep across all concurrency tiers."""
    if concurrency_levels is None:
        concurrency_levels = [1, 10, 50, 100, 250, 500]

    all_repetition_metrics: list[dict[int, dict[str, Any]]] = []
    all_raw_samples: list[dict[str, Any]] = []

    print(f"\nExecuting Class B1 HTTP Successful-Inference Capacity Benchmark against http://{host}:{port}{endpoint}")
    print(f"Concurrency Tiers: {concurrency_levels}")
    print(f"Target Requests per Tier per Repetition: {target_requests_per_tier}")
    print(f"Repetitions: {repetitions} (with isolated server lifecycles and deterministic state resets)")
    print("\n+-----+-----+-----------+---------+-------+-------+---------+-----------+-----------+----------+---------+---------+---------+---------+----------------+")
    print("|   C | Rep | Attempted |     2xx |   4xx |   5xx | Timeout | Exception | Success % | 2xx Mean | 2xx p50 | 2xx p95 | 2xx p99 | 2xx Max | Successful RPS |")
    print("+-----+-----+-----------+---------+-------+-------+---------+-----------+-----------+----------+---------+---------+---------+---------+----------------+")

    for rep in range(repetitions):
        rep_num = rep + 1
        print(f"\n--- Starting Repetition {rep_num}/{repetitions} (Fresh Uvicorn Subprocess) ---")
        server_proc = start_uvicorn_server(port=port, host=host, benchmark_mode=True)
        ready = wait_for_server_ready(port=port, host=host, timeout=35.0)
        if not ready:
            stop_uvicorn_server(server_proc)
            raise RuntimeError(f"Server failed to become ready on {host}:{port} for Repetition {rep_num}")

        # Warm-up phase: 10 requests to warm up PyTorch model & JIT
        url = f"http://{host}:{port}{endpoint}"
        async with httpx.AsyncClient(timeout=10.0) as warmup_client:
            for w_i in range(10):
                w_payload = {
                    "transaction_id": f"tx_warmup_{rep}_{w_i}",
                    "account_id": "acc_warmup",
                    "amount": 100.0,
                    "currency": "EUR",
                    "merchant_id": "merch_grocery_warmup",
                    "country": "US",
                    "device_id": "dev_warmup",
                }
                with contextlib.suppress(Exception):
                    await warmup_client.post(url, json=w_payload, headers={"X-Bank-ID": "bank_alpha"})

        # Immediately purge warm-up state so counters and memory are 100% clean
        reset_server_benchmark_state(port=port, host=host)

        rep_dict: dict[int, dict[str, Any]] = {}

        try:
            for c in concurrency_levels:
                tier_metrics, tier_samples = await run_single_concurrency_tier(
                    host=host,
                    port=port,
                    endpoint=endpoint,
                    concurrency=c,
                    target_requests=target_requests_per_tier,
                    repetition=rep_num,
                )
                rep_dict[c] = tier_metrics
                all_raw_samples.extend(tier_samples)

            all_repetition_metrics.append(rep_dict)

        finally:
            stop_uvicorn_server(server_proc)
            time.sleep(0.5)

    print("+-----+-----+-----------+---------+-------+-------+---------+-----------+-----------+----------+---------+---------+---------+---------+----------------+\n")

    # Aggregate statistics across repetitions for each concurrency tier
    aggregated_results: list[dict[str, Any]] = []
    for c in concurrency_levels:
        c_runs = [r[c] for r in all_repetition_metrics if c in r]
        if not c_runs:
            continue

        tot_attempted = sum(r["attempted"] for r in c_runs)
        tot_2xx = sum(r["status_2xx"] for r in c_runs)
        tot_4xx = sum(r["status_4xx"] for r in c_runs)
        tot_5xx = sum(r["status_5xx"] for r in c_runs)
        tot_to = sum(r["timeouts"] for r in c_runs)
        tot_exc = sum(r["exceptions"] for r in c_runs)
        tot_failed = sum(r["failed"] for r in c_runs)
        overall_success_rate = tot_2xx / max(1, tot_attempted)

        rps_vals = [r["successful_inference_rps"] for r in c_runs]
        p50_vals = [r["latency_2xx_p50_ms"] for r in c_runs]
        p95_vals = [r["latency_2xx_p95_ms"] for r in c_runs]
        p99_vals = [r["latency_2xx_p99_ms"] for r in c_runs]
        mean_vals = [r["latency_2xx_mean_ms"] for r in c_runs]
        max_vals = [r["latency_2xx_max_ms"] for r in c_runs]

        rps_mean = float(np.mean(rps_vals))
        rps_std = float(np.std(rps_vals, ddof=1)) if len(rps_vals) > 1 else 0.0
        rps_min = float(np.min(rps_vals))
        rps_max = float(np.max(rps_vals))

        p50_mean = float(np.mean(p50_vals))
        p50_std = float(np.std(p50_vals, ddof=1)) if len(p50_vals) > 1 else 0.0
        p50_min = float(np.min(p50_vals))
        p50_max = float(np.max(p50_vals))

        p95_mean = float(np.mean(p95_vals))
        p95_std = float(np.std(p95_vals, ddof=1)) if len(p95_vals) > 1 else 0.0
        p95_min = float(np.min(p95_vals))
        p95_max = float(np.max(p95_vals))

        p99_mean = float(np.mean(p99_vals))
        p99_std = float(np.std(p99_vals, ddof=1)) if len(p99_vals) > 1 else 0.0
        p99_min = float(np.min(p99_vals))
        p99_max = float(np.max(p99_vals))

        mean_lat = float(np.mean(mean_vals))
        mean_std = float(np.std(mean_vals, ddof=1)) if len(mean_vals) > 1 else 0.0
        max_lat = float(np.max(max_vals))

        aggregated_results.append({
            "concurrency": c,
            "target_requests_per_rep": target_requests_per_tier,
            "total_attempted_requests": tot_attempted,
            "status_2xx_total": tot_2xx,
            "status_4xx_total": tot_4xx,
            "status_5xx_total": tot_5xx,
            "timeouts_total": tot_to,
            "exceptions_total": tot_exc,
            "failed_total": tot_failed,
            "overall_success_rate": round(overall_success_rate, 4),
            "overall_failure_rate": round(tot_failed / max(1, tot_attempted), 4),
            # Primary Throughput: Successful HTTP 2xx inference transactions / duration
            "successful_inference_rps": {
                "mean": round(rps_mean, 1),
                "std": round(rps_std, 1),
                "min": round(rps_min, 1),
                "max": round(rps_max, 1),
            },
            # Top-level convenience fields for downstream compatibility
            "throughput_rps": round(rps_mean, 1),
            "p50_latency_ms": round(p50_mean, 2),
            "p95_latency_ms": round(p95_mean, 2),
            "p99_latency_ms": round(p99_mean, 2),
            "mean_latency_ms": round(mean_lat, 2),
            "max_latency_ms": round(max_lat, 2),
            # Primary Latency: Computed exclusively from successful 2xx responses
            "latency_2xx_p50_ms": {
                "mean": round(p50_mean, 2),
                "std": round(p50_std, 2),
                "min": round(p50_min, 2),
                "max": round(p50_max, 2),
            },
            "latency_2xx_p95_ms": {
                "mean": round(p95_mean, 2),
                "std": round(p95_std, 2),
                "min": round(p95_min, 2),
                "max": round(p95_max, 2),
            },
            "latency_2xx_p99_ms": {
                "mean": round(p99_mean, 2),
                "std": round(p99_std, 2),
                "min": round(p99_min, 2),
                "max": round(p99_max, 2),
            },
            "latency_2xx_mean_ms": {
                "mean": round(mean_lat, 2),
                "std": round(mean_std, 2),
            },
            "latency_2xx_max_ms": round(max_lat, 2),
            "repetition_runs": [
                {
                    "repetition": r["repetition"],
                    "attempted": r["attempted"],
                    "status_2xx": r["status_2xx"],
                    "status_4xx": r["status_4xx"],
                    "status_5xx": r["status_5xx"],
                    "timeouts": r["timeouts"],
                    "exceptions": r["exceptions"],
                    "duration_seconds": r["duration_seconds"],
                    "successful_inference_rps": r["successful_inference_rps"],
                    "attempted_request_rps": r["attempted_request_rps"],
                    "p50_ms": r["latency_2xx_p50_ms"],
                    "p95_ms": r["latency_2xx_p95_ms"],
                    "p99_ms": r["latency_2xx_p99_ms"],
                    "mean_ms": r["latency_2xx_mean_ms"],
                    "max_ms": r["latency_2xx_max_ms"],
                }
                for r in c_runs
            ],
        })

    # Summary table across repetitions
    print("\nClass B1 Aggregate Summary (Mean ± Sample SD across 3 Repetitions):")
    print("+-------------+---------------------+---------------------+---------------------+---------------------+--------------+")
    print("| Concurrency | Successful 2xx RPS  | 2xx p50 Latency     | 2xx p95 Latency     | 2xx p99 Latency     | Success Rate |")
    print("+-------------+---------------------+---------------------+---------------------+---------------------+--------------+")
    for row in aggregated_results:
        c_val = row["concurrency"]
        rps_str = f"{row['successful_inference_rps']['mean']:.1f} ± {row['successful_inference_rps']['std']:.1f} r/s"
        p50_str = f"{row['latency_2xx_p50_ms']['mean']:.2f} ± {row['latency_2xx_p50_ms']['std']:.2f} ms"
        p95_str = f"{row['latency_2xx_p95_ms']['mean']:.2f} ± {row['latency_2xx_p95_ms']['std']:.2f} ms"
        p99_str = f"{row['latency_2xx_p99_ms']['mean']:.2f} ± {row['latency_2xx_p99_ms']['std']:.2f} ms"
        succ_str = f"{row['overall_success_rate']:.1%}"
        print(f"| {c_val:<11} | {rps_str:>19} | {p50_str:>19} | {p95_str:>19} | {p99_str:>19} | {succ_str:>12} |")
    print("+-------------+---------------------+---------------------+---------------------+---------------------+--------------+\n")

    metadata = {
        "concurrency_semantics": "maximum simultaneously in-flight HTTP requests via closed-loop asynchronous worker pool",
        "server_lifecycle": "restarted_per_repetition_with_pre_tier_deterministic_state_reset",
        "warmup_sequence": "server_initialization -> 10_warmup_requests -> deterministic_reset -> measured_repetition",
        "tested_endpoint": endpoint,
        "target_url": f"http://{host}:{port}{endpoint}",
    }

    return aggregated_results, metadata, all_raw_samples


def run_full_http_benchmark(
    port: int = 8089,
    host: str = "127.0.0.1",
    endpoint: str = "/api/v1/score-transaction",
    concurrency_levels: list[int] | None = None,
    target_requests_per_tier: int = 1000,
    repetitions: int = 3,
    save_artifact: bool = True,
    output_path: Path | str | None = None,
) -> dict[str, Any]:
    """Executes the full Class B1 HTTP successful-inference capacity benchmark."""
    results, meta, raw_samples = asyncio.run(
        run_http_benchmark_sweep(
            host=host,
            port=port,
            endpoint=endpoint,
            concurrency_levels=concurrency_levels,
            target_requests_per_tier=target_requests_per_tier,
            repetitions=repetitions,
        )
    )

    payload = {
        "timestamp_utc": datetime.datetime.now(datetime.UTC).isoformat(),
        "benchmark_type": "CLASS_B1_LOCAL_HTTP_INFERENCE_CAPACITY_BENCHMARK",
        "benchmark_name": "Class B1 Local HTTP Service Inference Capacity Benchmark",
        "scientific_question": (
            "How does the actual HTTP scoring service behave under concurrent load "
            "when requests are allowed to reach and execute the inference/scoring path? "
            "Measures real 2xx transaction capacity without SlowAPI rate-limit rejections."
        ),
        "primary_metric_population": "HTTP 2xx successful inference transactions exclusively",
        "scope": (
            "Measures client-observed wall-clock HTTP latency against a live running Uvicorn ASGI server "
            f"over local loopback TCP sockets (127.0.0.1:{port}{endpoint}) with SlowAPI rate-limiting isolated. "
            "Includes TCP framing, Uvicorn event loop dispatch, FastAPI middleware stack, Pydantic request validation, "
            "ModelService evaluation, RiskScoringEngine execution, and response transmission."
        ),
        "server_provenance": {
            "server_implementation": "uvicorn (0.47.0)",
            "worker_count": 1,
            "server_mode": "single-worker ASGI process",
            "host": host,
            "port": port,
            "tested_endpoint": endpoint,
            "rate_limiter_isolated": True,
            "benchmark_mode_flag": "CFI_BENCHMARK_MODE=1",
            "server_lifecycle": meta["server_lifecycle"],
        },
        "client_provenance": {
            "client_library": f"httpx ({httpx.__version__})",
            "load_model": "closed-loop concurrent async workers",
            "concurrency_semantics": meta["concurrency_semantics"],
            "connection_reuse": "keep-alive enabled with pooled connections",
            "max_connections": "c + 50",
            "max_keepalive_connections": "c + 50",
            "keepalive_expiry_seconds": 30.0,
            "timeouts": {
                "timeout": 60.0,
                "connect": 10.0,
                "read": 60.0,
                "write": 10.0,
                "pool": 30.0,
            },
        },
        "repetitions_count": repetitions,
        "target_requests_per_tier": target_requests_per_tier,
        "environment": get_hardware_environment(),
        "metadata": meta,
        "concurrency_scaling": results,
    }

    if save_artifact:
        base_dir = Path(__file__).resolve().parents[2]
        http_file = Path(output_path) if output_path else base_dir / "benchmarks" / "results" / "raw" / "latency_http_service_benchmark.json"
        http_file.parent.mkdir(parents=True, exist_ok=True)
        with open(http_file, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        print(f"[+] Saved Class B1 HTTP service benchmark results to {http_file}")

        samples_file = base_dir / "benchmarks" / "results" / "raw" / "latency_http_service_samples.json"
        samples_payload = {
            "schema_version": "2.0.0",
            "benchmark_type": "CLASS_B1_LOCAL_HTTP_INFERENCE_CAPACITY_SAMPLES",
            "total_samples": len(raw_samples),
            "description": (
                "Per-request structured sample log for Class B1 benchmark. "
                "Preserves latency_ms, status_code, success flag, timeout status, exception, "
                "worker_id, repetition index, and concurrency tier for independent audit reconstruction."
            ),
            "samples": raw_samples,
        }
        with open(samples_file, "w", encoding="utf-8") as f:
            json.dump(samples_payload, f, indent=2)
        print(f"[+] Saved Class B1 raw structured samples to {samples_file}")

    return payload


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run Class B1 Local HTTP Service Inference Capacity Benchmark")
    parser.add_argument("--port", type=int, default=8089, help="Port for local Uvicorn server")
    parser.add_argument("--endpoint", type=str, default="/api/v1/score-transaction", help="Inference endpoint to test")
    parser.add_argument("--target-requests", type=int, default=1000, help="Target requests per concurrency tier")
    parser.add_argument("--repetitions", type=int, default=3, help="Number of benchmark repetitions")
    parser.add_argument("--no-save", action="store_true", help="Do not save output artifacts")
    parser.add_argument("--output-path", type=str, default=None, help="Custom output path for results JSON")
    args = parser.parse_args()

    run_full_http_benchmark(
        port=args.port,
        endpoint=args.endpoint,
        target_requests_per_tier=args.target_requests,
        repetitions=args.repetitions,
        save_artifact=not args.no_save,
        output_path=args.output_path,
    )
