"""Real-time transaction stream pipeline.

Architecture: Producer -> asyncio.Queue -> Consumer Workers -> Decision Log

This demonstrates actual event-driven transaction scoring: continuous transaction
events flow through an async queue, consumer workers call the real inference
endpoint (ASGI), decisions are published with latency metrics.

Usage:
    python scripts/transaction_stream.py --tps 50 --workers 10 --duration 30
"""

from __future__ import annotations

import asyncio
import json
import random
import statistics
import sys
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(_BACKEND))

import httpx
from app.main import app

BANK_IDS = ["bank_alpha", "bank_beta", "bank_gamma", "bank_delta", "bank_epsilon"]
MERCHANT_IDS = [
    "merchant_grocery", "merchant_crypto_exchange", "merchant_wire_transfer",
    "merchant_gambling", "merchant_electronics", "merchant_jewelry",
    "merchant_supermarket", "merchant_casino", "merchant_p2p_transfer",
]
CURRENCIES = ["EUR", "USD", "GBP", "CHF"]
COUNTRIES = ["DE", "FR", "US", "GB", "TR", "NL", "CH", "PL", "ES"]
HIGH_RISK_COUNTRIES = ["KP", "IR", "SY"]


@dataclass
class TxEvent:
    tx_id: str
    bank_id: str
    amount: float
    currency: str
    merchant_id: str
    country: str
    device_id: str
    produced_at: float = field(default_factory=time.perf_counter)


@dataclass
class Decision:
    tx_id: str
    bank_id: str
    risk_score: int
    risk_level: str
    decision: str
    latency_ms: float
    queue_wait_ms: float
    produced_at: float


async def producer(queue: asyncio.Queue, tps: float, duration_s: float, stats: dict) -> None:
    interval = 1.0 / tps
    deadline = time.perf_counter() + duration_s
    count = 0
    while time.perf_counter() < deadline:
        t0 = time.perf_counter()
        hr = random.random() < 0.05
        event = TxEvent(
            tx_id=f"tx_{uuid.uuid4().hex[:12]}",
            bank_id=random.choice(BANK_IDS),
            amount=round(
                random.uniform(50000, 200000) if hr else random.uniform(5, 50000), 2
            ),
            currency=random.choice(CURRENCIES),
            merchant_id=random.choice(
                ["merchant_crypto_exchange", "merchant_gambling"] if hr else MERCHANT_IDS
            ),
            country=random.choice(HIGH_RISK_COUNTRIES if hr else COUNTRIES),
            device_id=f"dev_{uuid.uuid4().hex[:8]}",
        )
        await queue.put(event)
        count += 1
        stats["produced"] = count
        sl = interval - (time.perf_counter() - t0)
        if sl > 0:
            await asyncio.sleep(sl)
    stats["producer_done"] = True


async def consumer(
    worker_id: int,
    queue: asyncio.Queue,
    client: httpx.AsyncClient,
    decisions: list,
    stats: dict,
) -> None:
    while True:
        try:
            event: TxEvent = queue.get_nowait()
        except asyncio.QueueEmpty:
            if stats.get("producer_done") and queue.empty():
                break
            await asyncio.sleep(0.001)
            continue

        wait_ms = (time.perf_counter() - event.produced_at) * 1000.0
        payload = {
            "transaction_id": event.tx_id,
            "account_id": f"acc_{hash(event.bank_id) % 10000}",
            "amount": event.amount,
            "currency": event.currency,
            "merchant_id": event.merchant_id,
            "country": event.country,
            "device_id": event.device_id,
        }
        t0 = time.perf_counter()
        try:
            resp = await client.post(
                "/api/v1/transactions/score",
                json=payload,
                headers={
                    "X-Tenant-ID": event.bank_id,
                    "X-Bank-ID": event.bank_id,
                    "X-Forwarded-For": f"10.{worker_id}.{hash(event.tx_id) % 256}.1",
                },
                timeout=10.0,
            )
            lat = (time.perf_counter() - t0) * 1000.0
            if resp.status_code == 200:
                body = resp.json()
                decisions.append(
                    Decision(
                        tx_id=event.tx_id,
                        bank_id=event.bank_id,
                        risk_score=body.get("risk_score", 0),
                        risk_level=body.get("risk_level", "?"),
                        decision=body.get("decision", "?"),
                        latency_ms=round(lat, 2),
                        queue_wait_ms=round(wait_ms, 2),
                        produced_at=event.produced_at,
                    )
                )
                stats["scored"] = stats.get("scored", 0) + 1
            else:
                stats["errors"] = stats.get("errors", 0) + 1
        except Exception:
            stats["errors"] = stats.get("errors", 0) + 1
        finally:
            queue.task_done()


async def monitor(queue: asyncio.Queue, stats: dict, interval: float = 5.0) -> None:
    start = time.perf_counter()
    while not stats.get("pipeline_done"):
        await asyncio.sleep(interval)
        el = time.perf_counter() - start
        print(
            f"  [{el:5.1f}s] produced={stats.get('produced', 0):5d}  "
            f"scored={stats.get('scored', 0):5d}  errors={stats.get('errors', 0):3d}  "
            f"queue={queue.qsize():4d}  {stats.get('scored', 0) / max(el, 0.001):.1f} tx/s",
            flush=True,
        )


async def run_stream_pipeline(
    tps: float = 50.0,
    n_workers: int = 10,
    duration_s: float = 30.0,
) -> dict:
    print(f"\n{'='*65}", flush=True)
    print("  EVENT-DRIVEN TRANSACTION STREAM PIPELINE", flush=True)
    print(f"  TPS={tps}  Workers={n_workers}  Duration={duration_s}s", flush=True)
    print(f"{'='*65}\n", flush=True)

    queue: asyncio.Queue = asyncio.Queue(maxsize=int(tps * 10))
    decisions: list[Decision] = []
    stats: dict = {
        "produced": 0, "scored": 0, "errors": 0,
        "producer_done": False, "pipeline_done": False,
    }

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver", timeout=15.0
    ) as client:
        print("  [WARM-UP] Priming inference path...", flush=True)
        for bk in BANK_IDS[:3]:
            try:
                await client.post(
                    "/api/v1/transactions/score",
                    json={
                        "transaction_id": "wu", "account_id": "acc_0",
                        "amount": 100.0, "currency": "EUR",
                        "merchant_id": "merchant_grocery",
                        "country": "DE", "device_id": "dev_wu",
                    },
                    headers={"X-Tenant-ID": bk, "X-Bank-ID": bk},
                )
            except Exception:
                pass
        print("  [WARM-UP] Done.\n", flush=True)

        prod_task = asyncio.create_task(producer(queue, tps, duration_s, stats))
        cons_tasks = [
            asyncio.create_task(consumer(i, queue, client, decisions, stats))
            for i in range(n_workers)
        ]
        mon_task = asyncio.create_task(monitor(queue, stats, 5.0))

        t0_pipe = time.perf_counter()
        await prod_task
        await queue.join()
        for c in cons_tasks:
            c.cancel()
        mon_task.cancel()
        await asyncio.gather(*cons_tasks, mon_task, return_exceptions=True)
        dur = time.perf_counter() - t0_pipe
        stats["pipeline_done"] = True

    if not decisions:
        return {"error": "no decisions recorded"}

    lats = [d.latency_ms for d in decisions]
    waits = [d.queue_wait_ms for d in decisions]
    s = sorted(lats)
    n2 = len(s)

    def pct(p: float) -> float:
        return round(s[min(int(p * n2), n2 - 1)], 2)

    dcnt: dict = {}
    rcnt: dict = {}
    for d in decisions:
        dcnt[d.decision] = dcnt.get(d.decision, 0) + 1
        rcnt[d.risk_level] = rcnt.get(d.risk_level, 0) + 1

    return {
        "pipeline_config": {
            "target_tps": tps, "n_workers": n_workers, "duration_s": duration_s,
        },
        "throughput": {
            "produced": stats["produced"],
            "scored": stats["scored"],
            "errors": stats["errors"],
            "pipeline_duration_s": round(dur, 2),
            "actual_scoring_tps": round(stats["scored"] / dur, 1),
            "pipeline_utilization_pct": round(
                stats["scored"] / max(stats["produced"], 1) * 100, 1
            ),
        },
        "latency_ms": {
            "min": pct(0), "p50": pct(0.5),
            "mean": round(statistics.mean(lats), 2),
            "p90": pct(0.9), "p95": pct(0.95), "p99": pct(0.99),
            "max": pct(1.0),
            "stdev": round(statistics.stdev(lats), 2) if n2 > 1 else 0.0,
        },
        "queue_wait_ms": {
            "mean": round(statistics.mean(waits), 2),
            "p99": round(sorted(waits)[min(int(0.99 * len(waits)), len(waits) - 1)], 2),
            "max": round(max(waits), 2),
        },
        "fraud_detection": {
            "decisions": dcnt,
            "risk_levels": rcnt,
            "block_rate_pct": round(dcnt.get("BLOCK", 0) / max(n2, 1) * 100, 2),
            "review_rate_pct": round(dcnt.get("REVIEW", 0) / max(n2, 1) * 100, 2),
            "allow_rate_pct": round(dcnt.get("ALLOW", 0) / max(n2, 1) * 100, 2),
        },
    }


def print_pipeline_report(report: dict) -> None:
    def _p(line: str) -> None:
        try:
            print(line, flush=True)
        except UnicodeEncodeError:
            print(line.encode("ascii", "replace").decode("ascii"), flush=True)

    thr = report["throughput"]
    lat = report["latency_ms"]
    qw = report["queue_wait_ms"]
    fraud = report["fraud_detection"]

    _p(f"\n{'='*65}")
    _p("  PIPELINE RESULTS")
    _p(f"{'='*65}")
    _p("\n  [THROUGHPUT]")
    _p(f"    Target TPS       : {report['pipeline_config']['target_tps']}")
    _p(f"    Actual TPS       : {thr['actual_scoring_tps']}")
    _p(f"    Produced         : {thr['produced']}")
    _p(f"    Scored           : {thr['scored']}")
    _p(f"    Errors           : {thr['errors']}")
    _p(f"    Utilization      : {thr['pipeline_utilization_pct']}%")
    _p(f"    Duration         : {thr['pipeline_duration_s']}s")
    _p("\n  [SCORING LATENCY per transaction (end-to-end)]")
    _p(f"    min  : {lat['min']:.2f} ms")
    _p(f"    p50  : {lat['p50']:.2f} ms")
    _p(f"    p90  : {lat['p90']:.2f} ms")
    _p(f"    p95  : {lat['p95']:.2f} ms")
    _p(f"    p99  : {lat['p99']:.2f} ms")
    _p(f"    max  : {lat['max']:.2f} ms")
    _p(f"    mean : {lat['mean']:.2f} +- {lat['stdev']:.2f} ms")
    _p("\n  [QUEUE WAIT (time sitting in stream before consumer picks up)]")
    _p(f"    mean : {qw['mean']:.2f} ms")
    _p(f"    p99  : {qw['p99']:.2f} ms")
    _p(f"    max  : {qw['max']:.2f} ms")
    _p("\n  [FRAUD DETECTION OUTCOMES]")
    for dec, cnt in sorted(fraud["decisions"].items()):
        pct_val = round(cnt / max(thr["scored"], 1) * 100, 1)
        _p(f"    {dec:<8}: {cnt:5d} ({pct_val:5.1f}%)")
    _p(f"\n  Block rate  : {fraud['block_rate_pct']:.2f}%  (injected ~5% high-risk)")
    _p(f"  Review rate : {fraud['review_rate_pct']:.2f}%")
    _p(f"  Allow rate  : {fraud['allow_rate_pct']:.2f}%")
    _p(f"\n{'='*65}\n")


if __name__ == "__main__":
    import argparse

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    p = argparse.ArgumentParser(description="Event-driven real-time transaction stream")
    p.add_argument("--tps", type=float, default=50.0)
    p.add_argument("--workers", type=int, default=10)
    p.add_argument("--duration", type=float, default=30.0)
    p.add_argument("--output", type=str, default="reports/stream_pipeline_results.json")
    args = p.parse_args()

    report = asyncio.run(
        run_stream_pipeline(tps=args.tps, n_workers=args.workers, duration_s=args.duration)
    )

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")

    print_pipeline_report(report)
    print(f"[+] Results saved to {out}", flush=True)
