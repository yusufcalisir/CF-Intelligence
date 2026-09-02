"""Locust Load Testing Suite for Real-Time Inference Gateway & API Layer.

Simulates concurrent banking client nodes, automated payment streams,
and compliance investigators to benchmark SLA adherence (<100ms p99 latency),
throughput capacity (TPS), and error rates.

Usage:
    # Headless 60-second load test with 50 concurrent users
    locust -f scripts/locustfile.py --headless -u 50 -r 10 --run-time 60s --host http://localhost:8000

    # Web UI mode
    locust -f scripts/locustfile.py --host http://localhost:8000
"""

from __future__ import annotations

import random
import uuid
from typing import Any

from locust import FastHttpUser, between, task


_BANK_IDS = ["bank_alpha", "bank_beta", "bank_gamma"]
_CURRENCIES = ["EUR", "USD", "GBP", "CHF"]
_MERCHANTS = ["crypto_exchange", "electronics", "wire_transfer", "gambling", "retail", "jewelry"]


class BankingNodeUser(FastHttpUser):
    """Simulates active banking nodes streaming real-time transactions for scoring."""

    wait_time = between(0.01, 0.05)  # High frequency payment stream (20-100 req/s per user)

    @task(60)
    def score_realtime_transaction(self) -> None:
        """Benchmark /api/v1/predict endpoint for real-time <100ms inference SLA."""
        tx_id = f"txn_{uuid.uuid4().hex[:12]}"
        bank_id = random.choice(_BANK_IDS)
        amount = round(random.uniform(5.0, 50000.0), 2)
        merchant = random.choice(_MERCHANTS)

        payload: dict[str, Any] = {
            "transaction_id": tx_id,
            "bank_id": bank_id,
            "source_bank_id": bank_id,
            "amount": amount,
            "currency": random.choice(_CURRENCIES),
            "merchant_category": merchant,
            "sender_account": f"DE893704004405320130{random.randint(10, 99)}",
            "receiver_account": f"GB29NWBK601613319268{random.randint(10, 99)}",
            "device_fingerprint": f"dev_fp_{uuid.uuid4().hex[:8]}",
            "country_code": random.choice(["DE", "FR", "US", "GB", "NL", "TR"]),
            "timestamp": "2026-09-02T05:00:00Z",
        }

        headers = {
            "X-Tenant-ID": bank_id,
            "X-Bank-ID": bank_id,
            "Content-Type": "application/json",
        }

        with self.client.post(
            "/api/v1/predict",
            json=payload,
            headers=headers,
            name="POST /api/v1/predict",
            catch_response=True,
        ) as response:
            if response.status_code == 200:
                data = response.json()
                if "risk_score" in data or "composite_risk_score" in data:
                    response.success()
                else:
                    response.failure(f"Missing risk score in payload: {response.text}")
            elif response.status_code == 429:
                # Rate limited under extreme burst (expected at high concurrency)
                response.success()
            else:
                response.failure(f"Unexpected status code: {response.status_code}")

    @task(20)
    def score_v1_inference_endpoint(self) -> None:
        """Benchmark /v1/inference/score endpoint."""
        bank_id = random.choice(_BANK_IDS)
        payload = {
            "transaction_id": f"txn_{uuid.uuid4().hex[:12]}",
            "source_bank_id": bank_id,
            "amount": round(random.uniform(10.0, 150000.0), 2),
            "currency": "EUR",
            "sender_account": f"DE893704004405320130{random.randint(10, 99)}",
            "receiver_account": f"GB29NWBK601613319268{random.randint(10, 99)}",
            "merchant_category": random.choice(_MERCHANTS),
            "device_fingerprint": f"dev_fp_{uuid.uuid4().hex[:8]}",
        }

        self.client.post(
            "/v1/inference/score",
            json=payload,
            headers={"X-Tenant-ID": bank_id, "X-Bank-ID": bank_id},
            name="POST /v1/inference/score",
        )

    @task(10)
    def check_health_readiness(self) -> None:
        """Verify low-overhead liveness & readiness probes."""
        self.client.get("/health", name="GET /health")
        self.client.get("/ready", name="GET /ready")


class ComplianceAnalystUser(FastHttpUser):
    """Simulates compliance officers reviewing cases and authenticating."""

    wait_time = between(0.1, 0.5)

    def on_start(self) -> None:
        """Log in on session start."""
        self.auth_token = ""
        res = self.client.post(
            "/api/v1/auth/login",
            json={"username": "investigator_alpha", "password": "DemoPassword123!"},
            name="POST /api/v1/auth/login",
        )
        if res.status_code == 200:
            self.auth_token = res.json().get("access_token", "")

    @task(15)
    def fetch_cases_list(self) -> None:
        """Query case workbench cases with tenant isolation."""
        headers = {
            "Authorization": f"Bearer {self.auth_token}" if self.auth_token else "",
            "X-Tenant-ID": "bank_alpha",
            "X-Bank-ID": "bank_alpha",
        }
        self.client.get("/api/v1/cases", headers=headers, name="GET /api/v1/cases")

    @task(5)
    def check_lockout_status(self) -> None:
        """Query brute-force lockout status."""
        self.client.get(
            "/api/v1/auth/lockout-status?username=investigator_alpha",
            name="GET /api/v1/auth/lockout-status",
        )
