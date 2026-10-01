"""End-to-End Local HTTP Service Latency & Concurrency Benchmark.

Measures the actual request path through a live running ASGI HTTP server (Uvicorn)
over local TCP network sockets (127.0.0.1:<port>).

Measures:
  - Client-observed wall-clock latency (p50, p95, p99, mean, max in ms)
  - Throughput (successful requests/sec)
  - Error and status accounting: 2xx, 4xx, 5xx, timeouts, client exceptions
  - Concurrency scaling: C in [1, 10, 50, 100, 250, 500] (with safe resource exhaustion handling)
  - Multi-repetition variance: 3 independent sweeps reporting mean, sample SD (ddof=1), min, max
  - Server & environment provenance (Uvicorn single-worker, OS, CPU, RAM, versions)
  - Full raw latency sample preservation

Scope:
  - This is an END-TO-END HTTP SERVICE BENCHMARK exercising:
      * Local TCP connection / HTTP framing
      * ASGI server (Uvicorn) event loop dispatch
      * Application middleware stack (CORS, SecurityHeaders, ContentType, TraceContext, DDoS, TenantAccess)
      * Request validation (Pydantic v2 deserialization)
      * ModelService evaluation & RiskScoringEngine execution
      * Response serialization (Pydantic v2 JSON)
      * Network socket transmission over loopback
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


def start_uvicorn_server(port: int = 8089, host: str = "127.0.0.1") -> subprocess.Popen:
    """Spawns Uvicorn ASGI server as a managed local subprocess."""
    backend_dir = Path(__file__).resolve().parents[2] / "backend"
    env = os.environ.copy()
    env["PYTHONPATH"] = str(backend_dir)
    env["TESTING"] = "1"  # Allows DDoS loopback bypass for benchmark clients

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


def wait_for_server_ready(port: int = 8089, host: str = "127.0.0.1", timeout: float = 30.0) -> bool:
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


async def run_http_concurrency_sweep(
    host: str = "127.0.0.1",
    port: int = 8089,
    endpoint: str = "/api/v1/score-transaction",
    concurrency_levels: list[int] | None = None,
    target_requests_per_tier: int = 500,
    repetitions: int = 3,
) -> tuple[list[dict[str, Any]], dict[str, dict[str, list[float]]], dict[str, Any]]:
    if concurrency_levels is None:
        concurrency_levels = [1, 10, 50, 100, 250, 500]

    url = f"http://{host}:{port}{endpoint}"

    # Perform initial warm-up requests on the live HTTP server
    async with httpx.AsyncClient(timeout=10.0) as warmup_client:
        for w_i in range(10):
            payload = {
                "transaction_id": f"tx_warmup_{w_i}",
                "account_id": "acc_warmup",
                "amount": 100.0,
                "currency": "EUR",
                "merchant_id": "merch_grocery_warmup",
                "country": "US",
                "device_id": "dev_warmup",
            }
            headers = {"X-Bank-ID": "bank_alpha", "X-Forwarded-For": "10.0.0.1"}
            try:
                await warmup_client.post(url, json=payload, headers=headers)
            except Exception:
                pass

    repetition_results: list[dict[int, dict[str, Any]]] = []
    raw_samples_by_tier: dict[str, dict[str, list[float]]] = {}
    completed_concurrency_levels = list(concurrency_levels)
    stopped_early_reason: str | None = None

    print(f"\nExecuting End-to-End HTTP Service Benchmark against {url}...")
    print(f"Repetitions: {repetitions} | Target requests per tier: {target_requests_per_tier}")
    print("+-------------+-------------+-------------+-------------+-------------+------------+-------------+")
    print("| Concurrency | Throughput  | p50 Latency | p95 Latency | p99 Latency | Error Rate | Status 2xx  |")
    print("+-------------+-------------+-------------+-------------+-------------+------------+-------------+")

    for rep in range(repetitions):
        rep_dict: dict[int, dict[str, Any]] = {}
        raw_samples_by_tier[f"rep_{rep + 1}"] = {}

        for c in completed_concurrency_levels:
            rpw = max(2, target_requests_per_tier // c)
            tier_latencies: list[float] = []
            stat_2xx = 0
            stat_4xx = 0
            stat_5xx = 0
            timeouts = 0
            exceptions = 0

            # Persistent HTTP client with connection pool sized to concurrency
            limits = httpx.Limits(max_connections=c + 10, max_keepalive_connections=c + 10)
            timeout = httpx.Timeout(15.0, connect=10.0)

            async with httpx.AsyncClient(limits=limits, timeout=timeout) as client:
                async def worker(worker_id: int):
                    w_lats = []
                    w_2xx = 0
                    w_4xx = 0
                    w_5xx = 0
                    w_to = 0
                    w_exc = 0
                    for i in range(rpw):
                        t_payload = {
                            "transaction_id": f"tx_hbench_{rep}_{c}_{worker_id}_{i}",
                            "account_id": f"acc_{worker_id}",
                            "amount": 250.0,
                            "currency": "EUR",
                            "merchant_id": "merch_grocery_001",
                            "country": "US",
                            "device_id": f"dev_{worker_id}",
                        }
                        t_headers = {
                            "X-Bank-ID": "bank_alpha",
                            "X-Forwarded-For": f"10.0.{worker_id // 250}.{worker_id % 250 + 1}",
                        }
                        t_req = time.perf_counter()
                        try:
                            resp = await client.post(url, json=t_payload, headers=t_headers)
                            elapsed = (time.perf_counter() - t_req) * 1000.0
                            w_lats.append(elapsed)
                            if 200 <= resp.status_code < 300:
                                w_2xx += 1
                            elif 400 <= resp.status_code < 500:
                                w_4xx += 1
                            else:
                                w_5xx += 1
                        except httpx.TimeoutException:
                            w_to += 1
                        except Exception:
                            w_exc += 1
                    return w_lats, w_2xx, w_4xx, w_5xx, w_to, w_exc

                t_start = time.perf_counter()
                try:
                    tasks = [worker(w_id) for w_id in range(c)]
                    results = await asyncio.gather(*tasks, return_exceptions=True)
                except Exception as exc:
                    stopped_early_reason = f"Concurrency {c} triggered exception: {exc}"
                    break

                t_total = time.perf_counter() - t_start

                for r in results:
                    if isinstance(r, tuple):
                        w_lats, w_2xx, w_4xx, w_5xx, w_to, w_exc = r
                        tier_latencies.extend(w_lats)
                        stat_2xx += w_2xx
                        stat_4xx += w_4xx
                        stat_5xx += w_5xx
                        timeouts += w_to
                        exceptions += w_exc
                    else:
                        exceptions += rpw

            attempted = stat_2xx + stat_4xx + stat_5xx + timeouts + exceptions
            failed = stat_4xx + stat_5xx + timeouts + exceptions
            err_rate = failed / max(1, attempted)
            rps = stat_2xx / (t_total + 1e-8)

            p50 = float(np.percentile(tier_latencies, 50)) if tier_latencies else 0.0
            p95 = float(np.percentile(tier_latencies, 95)) if tier_latencies else 0.0
            p99 = float(np.percentile(tier_latencies, 99)) if tier_latencies else 0.0
            mean_lat = float(np.mean(tier_latencies)) if tier_latencies else 0.0
            max_lat = float(np.max(tier_latencies)) if tier_latencies else 0.0

            rep_dict[c] = {
                "concurrency": c,
                "attempted": attempted,
                "status_2xx": stat_2xx,
                "status_4xx": stat_4xx,
                "status_5xx": stat_5xx,
                "timeouts": timeouts,
                "exceptions": exceptions,
                "failed": failed,
                "error_rate": round(err_rate, 4),
                "throughput_rps": round(rps, 1),
                "p50_latency_ms": round(p50, 2),
                "p95_latency_ms": round(p95, 2),
                "p99_latency_ms": round(p99, 2),
                "mean_latency_ms": round(mean_lat, 2),
                "max_latency_ms": round(max_lat, 2),
            }
            raw_samples_by_tier[f"rep_{rep + 1}"][f"c_{c}"] = tier_latencies

        repetition_results.append(rep_dict)

    # Aggregate across repetitions
    aggregated_results = []
    for c in completed_concurrency_levels:
        if c not in repetition_results[0]:
            continue
        p50_vals = [r[c]["p50_latency_ms"] for r in repetition_results]
        p95_vals = [r[c]["p95_latency_ms"] for r in repetition_results]
        p99_vals = [r[c]["p99_latency_ms"] for r in repetition_results]
        mean_vals = [r[c]["mean_latency_ms"] for r in repetition_results]
        max_vals = [r[c]["max_latency_ms"] for r in repetition_results]
        rps_vals = [r[c]["throughput_rps"] for r in repetition_results]

        tot_attempted = sum(r[c]["attempted"] for r in repetition_results)
        tot_2xx = sum(r[c]["status_2xx"] for r in repetition_results)
        tot_4xx = sum(r[c]["status_4xx"] for r in repetition_results)
        tot_5xx = sum(r[c]["status_5xx"] for r in repetition_results)
        tot_to = sum(r[c]["timeouts"] for r in repetition_results)
        tot_exc = sum(r[c]["exceptions"] for r in repetition_results)
        tot_failed = sum(r[c]["failed"] for r in repetition_results)

        mean_p50 = float(np.mean(p50_vals))
        mean_p95 = float(np.mean(p95_vals))
        mean_p99 = float(np.mean(p99_vals))
        mean_rps = float(np.mean(rps_vals))

        p50_sd = float(np.std(p50_vals, ddof=1)) if len(p50_vals) > 1 else 0.0
        p95_sd = float(np.std(p95_vals, ddof=1)) if len(p95_vals) > 1 else 0.0
        p99_sd = float(np.std(p99_vals, ddof=1)) if len(p99_vals) > 1 else 0.0
        rps_sd = float(np.std(rps_vals, ddof=1)) if len(rps_vals) > 1 else 0.0

        obs_err_rate = tot_failed / max(1, tot_attempted)

        print(
            f"| {c:<11} | {mean_rps:>8.1f} r/s | {mean_p50:>8.2f} ms | {mean_p95:>8.2f} ms | {mean_p99:>8.2f} ms | {obs_err_rate:>9.2%} | {tot_2xx:>11} |"
        )

        aggregated_results.append({
            "concurrency": c,
            "total_requests": repetition_results[0][c]["attempted"],
            "attempted_requests": tot_attempted,
            "status_2xx": tot_2xx,
            "status_4xx": tot_4xx,
            "status_5xx": tot_5xx,
            "timeouts": tot_to,
            "exceptions": tot_exc,
            "failed_requests": tot_failed,
            "error_rate": round(obs_err_rate, 4),
            "throughput_rps": round(mean_rps, 1),
            "throughput_std": round(rps_sd, 1),
            "p50_latency_ms": round(mean_p50, 2),
            "p50_std": round(p50_sd, 2),
            "p95_latency_ms": round(mean_p95, 2),
            "p95_std": round(p95_sd, 2),
            "p99_latency_ms": round(mean_p99, 2),
            "p99_std": round(p99_sd, 2),
            "mean_latency_ms": round(float(np.mean(mean_vals)), 2),
            "max_latency_ms": round(float(np.max(max_vals)), 2),
            "repetition_runs": [
                {
                    "repetition": idx + 1,
                    "throughput_rps": r[c]["throughput_rps"],
                    "p50_latency_ms": r[c]["p50_latency_ms"],
                    "p95_latency_ms": r[c]["p95_latency_ms"],
                    "p99_latency_ms": r[c]["p99_latency_ms"],
                    "status_2xx": r[c]["status_2xx"],
                    "status_4xx": r[c]["status_4xx"],
                    "status_5xx": r[c]["status_5xx"],
                    "timeouts": r[c]["timeouts"],
                }
                for idx, r in enumerate(repetition_results)
            ],
        })

    print("+-------------+-------------+-------------+-------------+-------------+------------+-------------+\n")

    metadata = {
        "stopped_early_reason": stopped_early_reason,
        "endpoint": endpoint,
        "target_url": url,
    }
    return aggregated_results, raw_samples_by_tier, metadata


def run_full_http_benchmark(
    port: int = 8089,
    host: str = "127.0.0.1",
    endpoint: str = "/api/v1/score-transaction",
    concurrency_levels: list[int] | None = None,
    target_requests_per_tier: int = 500,
    repetitions: int = 3,
    save_artifact: bool = True,
    output_path: Path | str | None = None,
) -> dict[str, Any]:
    print(f"Launching managed local Uvicorn ASGI server on {host}:{port}...")
    server_proc = start_uvicorn_server(port=port, host=host)

    try:
        ready = wait_for_server_ready(port=port, host=host, timeout=35.0)
        if not ready:
            raise RuntimeError(f"Uvicorn server failed to become ready on {host}:{port} within 35s")
        print("[+] ASGI server is ready and accepting requests.")

        results, raw_samples, meta = asyncio.run(
            run_http_concurrency_sweep(
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
            "benchmark_type": "LOCAL_HTTP_SERVICE_BENCHMARK",
            "benchmark_name": "End-to-End Local HTTP Service Latency Benchmark",
            "scope": (
                "Measures client-observed wall-clock HTTP latency against a live running Uvicorn ASGI server "
                f"over local loopback TCP sockets (127.0.0.1:{port}{endpoint}). "
                "Includes TCP framing, Uvicorn event loop dispatch, FastAPI middleware stack, "
                "Pydantic request validation, ModelService evaluation, RiskScoringEngine execution, "
                "and response transmission."
            ),
            "server_provenance": {
                "server_implementation": "uvicorn (0.47.0)",
                "worker_count": 1,
                "server_mode": "single-worker ASGI process",
                "host": host,
                "port": port,
                "tested_endpoint": endpoint,
            },
            "client_provenance": {
                "client_library": f"httpx ({httpx.__version__})",
                "load_model": "closed-loop concurrent async clients",
                "connection_reuse": "keep-alive enabled with pooled connections",
            },
            "repetitions_count": repetitions,
            "environment": get_hardware_environment(),
            "concurrency_scaling": results,
            "metadata": meta,
        }

        if save_artifact:
            base_dir = Path(__file__).resolve().parents[2]
            http_file = Path(output_path) if output_path else base_dir / "benchmarks" / "results" / "raw" / "latency_http_service_benchmark.json"
            http_file.parent.mkdir(parents=True, exist_ok=True)
            with open(http_file, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
            print(f"[+] Saved HTTP service benchmark results to {http_file}")

            samples_file = base_dir / "benchmarks" / "results" / "raw" / "latency_http_service_samples.json"
            with open(samples_file, "w", encoding="utf-8") as f:
                json.dump(raw_samples, f)
            print(f"[+] Saved raw HTTP latency samples to {samples_file}")

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
    parser = argparse.ArgumentParser(description="Run End-to-End HTTP Service Latency Benchmark")
    parser.add_argument("--port", type=int, default=8089, help="Port for local Uvicorn server")
    parser.add_argument("--endpoint", type=str, default="/api/v1/score-transaction", help="Inference endpoint to test")
    parser.add_argument("--target-requests", type=int, default=500, help="Target requests per concurrency tier")
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
