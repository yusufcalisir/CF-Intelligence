"""Centralized Backend Test Data Factory Module.

Provides deterministic fixture generators, synthetic payload builders, and
canonical mocks for unit, integration, contract, and end-to-end test suites.
"""

from __future__ import annotations

import hashlib
from typing import Any

import numpy as np


class TestDataFactory:
    """Centralized factory for creating consistent, type-safe test payloads."""

    _counter: int = 1

    @classmethod
    def _next_id(cls, prefix: str = "ID") -> str:
        current = cls._counter
        cls._counter += 1
        return f"{prefix}-{current:05d}"

    @classmethod
    def create_transaction_dict(
        cls,
        amount: float = 25000.0,
        bank_id: str = "bank_a",
        is_fraud: int = 0,
        merchant_category: str = "crypto",
        country_code: str = "US",
        velocity_1h: float = 14.0,
        **overrides: Any,
    ) -> dict[str, Any]:
        """Generates a raw transaction dictionary conforming to ISO 20022 schemas."""
        txn_id = overrides.get("transaction_id", cls._next_id("TXN"))
        base = {
            "transaction_id": txn_id,
            "timestamp": "2026-08-14T10:00:00Z",
            "amount": amount,
            "currency": "USD",
            "sender_account": f"ACC-{bank_id.upper()}-SENDER-01",
            "receiver_account": f"ACC-{bank_id.upper()}-RECV-02",
            "merchant_category": merchant_category,
            "country_code": country_code,
            "device_type": "mobile_app",
            "velocity_1h": velocity_1h,
            "merchant_risk_score": 0.85 if is_fraud else 0.15,
            "customer_history_score": 0.10 if is_fraud else 0.90,
            "chargeback_count": 4 if is_fraud else 0,
            "account_age_days": 12 if is_fraud else 450,
            "bank_id": bank_id,
            "is_fraud": is_fraud,
        }
        base.update(overrides)
        return base

    @classmethod
    def create_alert_dict(
        cls,
        severity: str = "CRITICAL",
        bank_id: str = "bank_a",
        composite_score: float = 880.0,
        **overrides: Any,
    ) -> dict[str, Any]:
        """Generates a validated AML Alert payload."""
        alert_id = overrides.get("id", cls._next_id("ALT"))
        base = {
            "id": alert_id,
            "simulation_id": "sim_active_01",
            "round_number": 5,
            "timestamp": "2026-08-14T10:00:00Z",
            "transaction_id": f"TXN-{alert_id}",
            "sender_account": f"ACC-{bank_id.upper()}-001",
            "receiver_account": f"ACC-{bank_id.upper()}-002",
            "amount": 28500.0,
            "currency": "USD",
            "risk_score": composite_score,
            "composite_risk_score": composite_score,
            "severity": severity,
            "bank_id": bank_id,
            "reason_codes": ["HIGH_VELOCITY", "STRUCTURING_RISK"],
            "confidence": 0.94,
            "involved_entity_ids": [f"ENT-{alert_id}-A", f"ENT-{alert_id}-B"],
            "created_at": "2026-08-14T10:00:00Z",
            "top_features": [
                {"feature": "amount", "importance": 0.45},
                {"feature": "velocity_1h", "importance": 0.35},
                {"feature": "country_code", "importance": 0.20},
            ],
            "risk_factors": [
                {"factor": "Layering Velocity", "description": "Rapid multi-hop transfers detected", "score": 0.91}
            ],
            "model_confidence": 0.94,
        }
        base.update(overrides)
        return base

    @classmethod
    def create_case_dict(
        cls,
        status: str = "investigating",
        priority: str = "high",
        assigned_to: str | None = "analyst_01",
        **overrides: Any,
    ) -> dict[str, Any]:
        """Generates a Case Management record."""
        case_id = overrides.get("id", cls._next_id("CASE"))
        base = {
            "id": case_id,
            "title": f"Cross-Bank Fraud Case ({case_id})",
            "description": "Suspicious transaction cluster with velocity spikes.",
            "status": status,
            "priority": priority,
            "assigned_to": assigned_to,
            "alert_ids": [f"ALT-{case_id}-01", f"ALT-{case_id}-02"],
            "evidence_ids": [f"EVD-{case_id}-01"],
            "notes": [
                {
                    "id": f"NOTE-{case_id}-01",
                    "author": "analyst_01",
                    "content": "Initial inquiry started.",
                    "created_at": "2026-08-14T08:30:00Z",
                }
            ],
            "timeline": [
                {
                    "id": f"TL-{case_id}-01",
                    "event_type": "created",
                    "description": "Case auto-generated",
                    "actor": "system",
                    "timestamp": "2026-08-14T08:00:00Z",
                }
            ],
            "created_at": "2026-08-14T08:00:00Z",
            "updated_at": "2026-08-14T08:30:00Z",
            "closed_at": "2026-08-14T11:00:00Z" if status.startswith("closed_") else None,
            "total_risk_score": 880,
            "duration_hours": 2.5,
            "is_open": not status.startswith("closed_"),
        }
        base.update(overrides)
        return base

    @classmethod
    def create_evidence_dict(
        cls,
        case_id: str,
        content: str = '{"proof": "merkle_root_verified"}',
        **overrides: Any,
    ) -> dict[str, Any]:
        """Generates a cryptographic Evidence ledger item with deterministic SHA-256."""
        ev_id = overrides.get("id", cls._next_id("EVD"))
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        base = {
            "id": ev_id,
            "case_id": case_id,
            "evidence_type": "ledger_proof",
            "title": f"Merkle Audit Proof ({ev_id})",
            "file_path": f"/vault/{case_id}/{ev_id}.json",
            "content_hash": content_hash,
            "uploaded_by": "audit_daemon",
            "uploaded_at": "2026-08-14T08:35:00Z",
            "content": content,
        }
        base.update(overrides)
        return base

    @classmethod
    def create_rule_dict(
        cls,
        name: str = "Block Rapid Structuring",
        **overrides: Any,
    ) -> dict[str, Any]:
        """Generates a Business Rule with AST condition."""
        rule_id = overrides.get("id", cls._next_id("RULE"))
        base = {
            "id": rule_id,
            "name": name,
            "description": "Rule to intercept structuring and rapid layering.",
            "is_active": True,
            "action": "flag_for_review",
            "priority": 10,
            "created_at": "2026-08-14T08:00:00Z",
            "updated_at": None,
            "condition": {
                "and": [
                    {"field": "amount", "operator": "gte", "value": 9000},
                    {"field": "velocity", "operator": "gt", "value": 10},
                ]
            },
        }
        base.update(overrides)
        return base

    @classmethod
    def create_synthetic_weights(
        cls,
        layer_shapes: list[tuple] | None = None,
        seed: int = 42,
    ) -> list[np.ndarray]:
        """Generates deterministic synthetic model weights for federated aggregation testing."""
        rng = np.random.default_rng(seed)
        shapes = layer_shapes or [(16, 32), (32, 16), (16, 1)]
        return [rng.normal(0, 0.1, size=shape).astype(np.float32) for shape in shapes]

    @classmethod
    def create_synthetic_gradient_update(
        cls,
        is_byzantine: bool = False,
        seed: int = 42,
    ) -> list[np.ndarray]:
        """Generates clean or malicious Byzantine gradients."""
        weights = cls.create_synthetic_weights(seed=seed)
        if is_byzantine:
            # Sign-flip and scale attack
            return [-10.0 * w for w in weights]
        return weights
