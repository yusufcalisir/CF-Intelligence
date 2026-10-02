"""Class B1 v2: Single-Worker Localhost HTTP Benchmark.

Measures the actual request path through a live running ASGI HTTP server (Uvicorn)
over local TCP network sockets (127.0.0.1:<port>) with rate-limiting explicitly isolated.

Scientific Question:
  How does the single-worker HTTP scoring service behave under concurrent load
  when requests are allowed to reach and execute the inference/scoring path,
  and the load generator itself is verified not to be the limiting factor?

Primary Population:
  HTTP 2xx successful inference transactions. Non-2xx responses are recorded
  and reported separately, and are NEVER mixed into headline inference latency percentiles.

Methodology & Invariants:
  - Benchmark Rate-Limiting Isolation: Server is launched with CFI_BENCHMARK_MODE=1,
    which disables the SlowAPI rate limiter on the isolated benchmark server instance.
  - Separate OS Processes: Uvicorn server and load generator run in distinct OS processes.
    PIDs, thread counts, CPU usage, and RSS are tracked and recorded for both.
  - Client Health Verification: Asynchronous heartbeat task measures client event-loop lag
    (mean, p50, p95, p99, max) and process CPU to verify client health.
  - Verified Load Generator: Uses aiohttp by default to eliminate HTTPX client event-loop
    starvation at high concurrency.
  - Equivalent Starting Conditions & State Reset: Deterministic state reset (/api/v1/benchmark/reset)
    clears rate limiter storage, tenant metering, and triggers garbage collection before each tier.
  - Server Lifecycle: Clean Uvicorn subprocess per repetition with deterministic state resets.
  - Controlled Warm-Up: 10 warmup requests executed during server initialization, followed
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

import aiohttp
import httpx
import numpy as np
import psutil


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
        env["ram_total_gb"] = round(psutil.virtual_memory().total / (1024**3), 2)
    except Exception:
        env["ram_total_gb"] = None
    return env


def start_uvicorn_server(
    port: int = 8089,
    host: str = "127.0.0.1",
    benchmark_mode: bool = True,
) -> subprocess.Popen:
    """Spawns Uvicorn ASGI server as a managed local subprocess in a separate OS process."""
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


async def client_event_loop_heartbeat(lags_list: list[float], stop_event: asyncio.Event) -> None:
    """High-frequency event-loop heartbeat measuring scheduling lag in milliseconds."""
    interval_s = 0.005
    while not stop_event.is_set():
        t0 = time.perf_counter()
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval_s)
            break
        except TimeoutError:
            t1 = time.perf_counter()
            lag_ms = max(0.0, (t1 - t0 - interval_s) * 1000.0)
            lags_list.append(lag_ms)


async def run_single_concurrency_tier(
    host: str,
    port: int,
    endpoint: str,
    concurrency: int,
    target_requests: int,
    repetition: int,
    server_pid: int | None = None,
    client_library: str = "aiohttp",
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Executes a single concurrency tier with closed-loop workers, client loop tracking, and structured logging."""
    url = f"http://{host}:{port}{endpoint}"
    c = concurrency
    rpw = max(2, target_requests // c)
    _actual_attempted = rpw * c

    # Ensure deterministic state reset immediately prior to tier execution
    reset_ok = reset_server_benchmark_state(port=port, host=host)
    if not reset_ok:
        print(f"    [!] Warning: Benchmark reset returned non-200 prior to C={c}")

    tier_samples: list[dict[str, Any]] = []
    client_loop_lags: list[float] = []
    stop_event = asyncio.Event()

    # Client process tracking
    client_proc = psutil.Process()
    client_pid = client_proc.pid
    _client_threads_start = client_proc.num_threads()
    cpu_before = client_proc.cpu_times()

    # Server process tracking
    server_proc_obj = psutil.Process(server_pid) if server_pid and psutil.pid_exists(server_pid) else None
    _server_threads_start = server_proc_obj.num_threads() if server_proc_obj else None
    server_cpu_before = server_proc_obj.cpu_times() if server_proc_obj else None

    # Start event-loop heartbeat monitor
    hb_task = asyncio.create_task(client_event_loop_heartbeat(client_loop_lags, stop_event))

    t_start = time.perf_counter()

    if client_library == "aiohttp":
        conn = aiohttp.TCPConnector(
            limit=c + 50,
            limit_per_host=c + 50,
            keepalive_timeout=30.0,
            enable_cleanup_closed=True,
        )
        timeout = aiohttp.ClientTimeout(total=60.0, connect=10.0, sock_read=60.0)

        async with aiohttp.ClientSession(connector=conn, timeout=timeout) as session:
            async def worker_aiohttp(worker_id: int) -> list[dict[str, Any]]:
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
                        async with session.post(url, json=t_payload, headers=t_headers) as resp:
                            await resp.read()
                            elapsed = (time.perf_counter() - t_req) * 1000.0
                            w_samples.append({
                                "latency_ms": round(elapsed, 3),
                                "status_code": resp.status,
                                "success": 200 <= resp.status < 300,
                                "timeout": False,
                                "exception": None,
                                "worker_id": worker_id,
                                "repetition": repetition,
                                "concurrency": c,
                            })
                    except TimeoutError:
                        elapsed = (time.perf_counter() - t_req) * 1000.0
                        w_samples.append({
                            "latency_ms": round(elapsed, 3),
                            "status_code": None,
                            "success": False,
                            "timeout": True,
                            "exception": "TimeoutError",
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

            tasks = [worker_aiohttp(w_id) for w_id in range(c)]
            results = await asyncio.gather(*tasks, return_exceptions=True)

    else:
        # Historical HTTPX runner path
        limits = httpx.Limits(max_connections=c + 50, max_keepalive_connections=c + 50, keepalive_expiry=30.0)
        timeout = httpx.Timeout(timeout=60.0, connect=10.0, read=60.0, write=10.0, pool=30.0)

        async with httpx.AsyncClient(limits=limits, timeout=timeout) as client:
            async def worker_httpx(worker_id: int) -> list[dict[str, Any]]:
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

            tasks = [worker_httpx(w_id) for w_id in range(c)]
            results = await asyncio.gather(*tasks, return_exceptions=True)

    t_total = time.perf_counter() - t_start

    # Stop heartbeat and collect timings
    stop_event.set()
    await hb_task

    cpu_after = client_proc.cpu_times()
    client_cpu_user = cpu_after.user - cpu_before.user
    client_cpu_sys = cpu_after.system - cpu_before.system
    client_cpu_total = client_cpu_user + client_cpu_sys
    client_cpu_pct = (client_cpu_total / (t_total + 1e-8)) * 100.0
    client_core_equiv = client_cpu_total / (t_total + 1e-8)
    client_rss_mb = round(client_proc.memory_info().rss / (1024 * 1024), 2)
    client_threads_end = client_proc.num_threads()

    server_rss_mb = round(server_proc_obj.memory_info().rss / (1024 * 1024), 2) if server_proc_obj else None
    server_threads_end = server_proc_obj.num_threads() if server_proc_obj else None
    if server_proc_obj and server_cpu_before:
        srv_cpu_after = server_proc_obj.cpu_times()
        srv_cpu_total = (srv_cpu_after.user - server_cpu_before.user) + (srv_cpu_after.system - server_cpu_before.system)
        server_cpu_pct = (srv_cpu_total / (t_total + 1e-8)) * 100.0
        server_core_equiv = srv_cpu_total / (t_total + 1e-8)
    else:
        server_cpu_pct = None
        server_core_equiv = None

    for r in results:
        if isinstance(r, list):
            tier_samples.extend(r)
        else:
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

    # Client loop lag percentiles
    loop_p50 = float(np.percentile(client_loop_lags, 50)) if client_loop_lags else 0.0
    loop_p95 = float(np.percentile(client_loop_lags, 95)) if client_loop_lags else 0.0
    loop_p99 = float(np.percentile(client_loop_lags, 99)) if client_loop_lags else 0.0
    loop_max = float(np.max(client_loop_lags)) if client_loop_lags else 0.0
    loop_mean = float(np.mean(client_loop_lags)) if client_loop_lags else 0.0

    # Saturation classification: if client event loop or CPU saturates severely
    client_status = "HEALTHY"
    if loop_p99 > 200.0 or client_core_equiv > 0.90:
        client_status = "CLIENT_LIMITED"

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
        "client_health": {
            "client_pid": client_pid,
            "client_threads": client_threads_end,
            "client_rss_mb": client_rss_mb,
            "client_cpu_pct": round(client_cpu_pct, 1),
            "client_core_equiv": round(client_core_equiv, 2),
            "client_host_fraction_16c": round(client_core_equiv / 16.0, 4),
            "loop_lag_p50_ms": round(loop_p50, 2),
            "loop_lag_p95_ms": round(loop_p95, 2),
            "loop_lag_p99_ms": round(loop_p99, 2),
            "loop_lag_max_ms": round(loop_max, 2),
            "loop_lag_mean_ms": round(loop_mean, 2),
            "classification": client_status,
        },
        "server_health": {
            "server_pid": server_pid,
            "server_threads": server_threads_end,
            "server_rss_mb": server_rss_mb,
            "server_cpu_pct": round(server_cpu_pct, 1) if server_cpu_pct is not None else None,
            "server_core_equiv": round(server_core_equiv, 2) if server_core_equiv is not None else None,
        },
    }

    # Print Re-evaluation Result Table Row
    print(
        f"| {c:>3} | {repetition:>3} | {attempted:>9} | {stat_2xx:>7} | {stat_4xx:>5} | {stat_5xx:>5} | "
        f"{num_to:>7} | {num_exc:>9} | {success_rate:>8.1%} | {mean_2xx:>8.2f} | {p50_2xx:>7.2f} | "
        f"{p95_2xx:>7.2f} | {p99_2xx:>7.2f} | {max_2xx:>7.2f} | {successful_inference_rps:>14.1f} | "
        f"{loop_p99:>8.1f} | {client_core_equiv:>6.2f} |",
        flush=True,
    )

    return tier_metrics, tier_samples


async def run_http_benchmark_sweep(
    host: str = "127.0.0.1",
    port: int = 8089,
    endpoint: str = "/api/v1/score-transaction",
    concurrency_levels: list[int] | None = None,
    target_requests_per_tier: int = 1000,
    repetitions: int = 3,
    client_library: str = "aiohttp",
) -> tuple[list[dict[str, Any]], dict[str, Any], list[dict[str, Any]]]:
    """Coordinates multi-repetition benchmark sweep across all concurrency tiers."""
    if concurrency_levels is None:
        concurrency_levels = [1, 10, 50, 100, 250, 500]

    all_repetition_metrics: list[dict[int, dict[str, Any]]] = []
    all_raw_samples: list[dict[str, Any]] = []

    print(f"\nExecuting Class B1 v2 Local HTTP Successful-Inference Benchmark against http://{host}:{port}{endpoint}", flush=True)
    print(f"Load Generator: {client_library} (Separate OS process, client health instrumented)", flush=True)
    print(f"Concurrency Tiers: {concurrency_levels}", flush=True)
    print(f"Target Requests per Tier per Repetition: {target_requests_per_tier}", flush=True)
    print(f"Repetitions: {repetitions} (Fresh Uvicorn subprocess per rep, deterministic pre-tier reset)", flush=True)
    print("\n+-----+-----+-----------+---------+-------+-------+---------+-----------+-----------+----------+---------+---------+---------+---------+----------------+----------+--------+", flush=True)
    print("|   C | Rep | Attempted |     2xx |   4xx |   5xx | Timeout | Exception | Success % | 2xx Mean | 2xx p50 | 2xx p95 | 2xx p99 | 2xx Max | Successful RPS | Loop p99 | Cli CPU|", flush=True)
    print("+-----+-----+-----------+---------+-------+-------+---------+-----------+-----------+----------+---------+---------+---------+---------+----------------+----------+--------+", flush=True)

    for rep in range(repetitions):
        rep_num = rep + 1
        print(f"\n--- Starting Repetition {rep_num}/{repetitions} (Fresh Uvicorn Subprocess) ---", flush=True)
        server_proc = start_uvicorn_server(port=port, host=host, benchmark_mode=True)
        ready = wait_for_server_ready(port=port, host=host, timeout=35.0)
        if not ready:
            stop_uvicorn_server(server_proc)
            raise RuntimeError(f"Server failed to become ready on {host}:{port} for Repetition {rep_num}")

        server_pid = server_proc.pid

        # Controlled Warm-Up: 10 requests to warm up PyTorch model & JIT
        url = f"http://{host}:{port}{endpoint}"
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10.0)) as warmup_client:
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
                    async with warmup_client.post(url, json=w_payload, headers={"X-Bank-ID": "bank_alpha"}) as resp:
                        await resp.read()

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
                    server_pid=server_pid,
                    client_library=client_library,
                )
                rep_dict[c] = tier_metrics
                all_raw_samples.extend(tier_samples)

            all_repetition_metrics.append(rep_dict)

        finally:
            stop_uvicorn_server(server_proc)
            time.sleep(1.0)

    print("+-----+-----+-----------+---------+-------+-------+---------+-----------+-----------+----------+---------+---------+---------+---------+----------------+----------+--------+\n", flush=True)

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

        loop_p50_vals = [r["client_health"]["loop_lag_p50_ms"] for r in c_runs]
        loop_p95_vals = [r["client_health"]["loop_lag_p95_ms"] for r in c_runs]
        loop_p99_vals = [r["client_health"]["loop_lag_p99_ms"] for r in c_runs]
        loop_max_vals = [r["client_health"]["loop_lag_max_ms"] for r in c_runs]

        cli_cpu_vals = [r["client_health"]["client_core_equiv"] for r in c_runs]
        srv_cpu_vals = [r["server_health"]["server_core_equiv"] for r in c_runs if r["server_health"]["server_core_equiv"] is not None]

        # Calculate pooled p50 across all successful requests in all repetitions
        tier_all_2xx_lats = [
            s["latency_ms"]
            for s in all_raw_samples
            if s["concurrency"] == c and s["success"]
        ]
        pooled_p50 = float(np.percentile(tier_all_2xx_lats, 50)) if tier_all_2xx_lats else 0.0
        pooled_p95 = float(np.percentile(tier_all_2xx_lats, 95)) if tier_all_2xx_lats else 0.0
        pooled_p99 = float(np.percentile(tier_all_2xx_lats, 99)) if tier_all_2xx_lats else 0.0

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
            "successful_inference_rps": {
                "mean": round(rps_mean, 1),
                "std": round(rps_std, 1),
                "min": round(rps_min, 1),
                "max": round(rps_max, 1),
            },
            "throughput_rps": round(rps_mean, 1),
            "p50_latency_ms": round(p50_mean, 2),
            "p95_latency_ms": round(p95_mean, 2),
            "p99_latency_ms": round(p99_mean, 2),
            "mean_latency_ms": round(mean_lat, 2),
            "max_latency_ms": round(max_lat, 2),
            "pooled_p50_ms": round(pooled_p50, 2),
            "pooled_p95_ms": round(pooled_p95, 2),
            "pooled_p99_ms": round(pooled_p99, 2),
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
            "client_health_aggregate": {
                "loop_lag_p50_ms_mean": round(float(np.mean(loop_p50_vals)), 2),
                "loop_lag_p95_ms_mean": round(float(np.mean(loop_p95_vals)), 2),
                "loop_lag_p99_ms_mean": round(float(np.mean(loop_p99_vals)), 2),
                "loop_lag_max_ms_max": round(float(np.max(loop_max_vals)), 2),
                "client_core_equiv_mean": round(float(np.mean(cli_cpu_vals)), 2),
                "client_host_fraction_16c": round(float(np.mean(cli_cpu_vals)) / 16.0, 4),
            },
            "server_health_aggregate": {
                "server_core_equiv_mean": round(float(np.mean(srv_cpu_vals)), 2) if srv_cpu_vals else None,
            },
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
                    "client_health": r["client_health"],
                    "server_health": r["server_health"],
                }
                for r in c_runs
            ],
        })

    # Summary table across repetitions
    print("\nClass B1 v2 Aggregate Summary (Mean ± Sample SD across 3 Repetitions):", flush=True)
    print("+-------------+---------------------+---------------------+---------------------+---------------------+--------------+---------------+", flush=True)
    print("| Concurrency | Successful 2xx RPS  | 2xx p50 Latency     | 2xx p95 Latency     | 2xx p99 Latency     | Success Rate | Cli Loop p99  |", flush=True)
    print("+-------------+---------------------+---------------------+---------------------+---------------------+--------------+---------------+", flush=True)
    for row in aggregated_results:
        c_val = row["concurrency"]
        rps_str = f"{row['successful_inference_rps']['mean']:.1f} ± {row['successful_inference_rps']['std']:.1f} r/s"
        p50_str = f"{row['latency_2xx_p50_ms']['mean']:.2f} ± {row['latency_2xx_p50_ms']['std']:.2f} ms"
        p95_str = f"{row['latency_2xx_p95_ms']['mean']:.2f} ± {row['latency_2xx_p95_ms']['std']:.2f} ms"
        p99_str = f"{row['latency_2xx_p99_ms']['mean']:.2f} ± {row['latency_2xx_p99_ms']['std']:.2f} ms"
        succ_str = f"{row['overall_success_rate']:.1%}"
        cli_loop = f"{row['client_health_aggregate']['loop_lag_p99_ms_mean']:.2f} ms"
        print(f"| {c_val:<11} | {rps_str:>19} | {p50_str:>19} | {p95_str:>19} | {p99_str:>19} | {succ_str:>12} | {cli_loop:>13} |", flush=True)
    print("+-------------+---------------------+---------------------+---------------------+---------------------+--------------+---------------+\n", flush=True)

    metadata = {
        "benchmark_identity": "Class B1 v2: Single-Worker Localhost HTTP Benchmark",
        "concurrency_semantics": "maximum simultaneously in-flight HTTP requests via closed-loop asynchronous worker pool",
        "server_lifecycle": "restarted_per_repetition_with_pre_tier_deterministic_state_reset",
        "warmup_sequence": "server_initialization -> 10_warmup_requests -> deterministic_reset -> measured_repetition",
        "tested_endpoint": endpoint,
        "target_url": f"http://{host}:{port}{endpoint}",
        "percentile_semantics": {
            "per_tier_p50_mean": "Arithmetic mean of the 3 independent per-repetition sample p50 values",
            "pooled_p50": "Exact 50th percentile computed across all 3,000 pooled successful requests in the tier",
        },
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
    client_library: str = "aiohttp",
) -> dict[str, Any]:
    """Executes the full Class B1 v2 Single-Worker Localhost HTTP Benchmark."""
    results, meta, raw_samples = asyncio.run(
        run_http_benchmark_sweep(
            host=host,
            port=port,
            endpoint=endpoint,
            concurrency_levels=concurrency_levels,
            target_requests_per_tier=target_requests_per_tier,
            repetitions=repetitions,
            client_library=client_library,
        )
    )

    client_version = aiohttp.__version__ if client_library == "aiohttp" else httpx.__version__

    payload = {
        "timestamp_utc": datetime.datetime.now(datetime.UTC).isoformat(),
        "benchmark_type": "CLASS_B1_LOCAL_HTTP_INFERENCE_CAPACITY_BENCHMARK",
        "benchmark_name": "Class B1 v2: Single-Worker Localhost HTTP Benchmark",
        "scientific_question": (
            "What HTTP performance does the current single-worker application demonstrate "
            "when the load generator itself is verified not to be the observed high-concurrency limiting factor?"
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
            "client_library": f"{client_library} ({client_version})",
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
        print(f"[+] Saved Class B1 v2 HTTP benchmark results to {http_file}", flush=True)

        samples_file = base_dir / "benchmarks" / "results" / "raw" / "latency_http_service_samples.json"
        samples_payload = {
            "schema_version": "2.1.0",
            "benchmark_type": "CLASS_B1_LOCAL_HTTP_INFERENCE_CAPACITY_SAMPLES",
            "total_samples": len(raw_samples),
            "description": (
                "Per-request structured sample log for Class B1 v2 benchmark. "
                "Preserves latency_ms, status_code, success flag, timeout status, exception, "
                "worker_id, repetition index, and concurrency tier for independent audit reconstruction."
            ),
            "samples": raw_samples,
        }
        with open(samples_file, "w", encoding="utf-8") as f:
            json.dump(samples_payload, f, indent=2)
        print(f"[+] Saved Class B1 v2 raw structured samples to {samples_file}", flush=True)

    return payload


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run Class B1 v2 Local HTTP Service Benchmark")
    parser.add_argument("--port", type=int, default=8089, help="Port for local Uvicorn server")
    parser.add_argument("--endpoint", type=str, default="/api/v1/score-transaction", help="Inference endpoint to test")
    parser.add_argument("--target-requests", type=int, default=1000, help="Target requests per concurrency tier")
    parser.add_argument("--repetitions", type=int, default=3, help="Number of benchmark repetitions")
    parser.add_argument("--client", choices=["aiohttp", "httpx"], default="aiohttp", help="Load generator client library")
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
        client_library=args.client,
    )
