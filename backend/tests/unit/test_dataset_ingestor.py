"""Unit tests for Dataset Ingestion Studio API endpoints and Great Expectations contract gating."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest.mark.asyncio
async def test_validate_dataset_preview_inferred_signals() -> None:
    """Verify pre-flight preview infers canonical AML signals from custom headers."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/datasets/validate-preview",
            json={
                "filename": "transactions_sample.csv",
                "file_format": "csv",
                "raw_header": ["tx_time", "amt", "src_acc", "dest_acc", "channel", "is_fraud"],
                "sample_rows": [
                    {"tx_time": "2026-09-01T12:00:00Z", "amt": 1250.50, "src_acc": "ACC-100", "dest_acc": "ACC-200", "channel": "WIRE", "is_fraud": 0},
                    {"tx_time": "2026-09-01T12:05:00Z", "amt": 9800.00, "src_acc": "ACC-101", "dest_acc": "ACC-300", "channel": "ACH", "is_fraud": 1},
                ],
                "total_bytes": 10240,
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["preview_id"].startswith("PREV-")
        assert data["file_format"] == "csv"
        assert len(data["column_mappings"]) == 6

        # Check mapping inference
        mappings = {m["source_column"]: m["target_signal"] for m in data["column_mappings"]}
        assert mappings["amt"] == "transaction_amount"
        assert mappings["tx_time"] == "timestamp"
        assert mappings["src_acc"] == "source_account_id"
        assert mappings["dest_acc"] == "destination_account_id"
        assert mappings["is_fraud"] == "is_fraud"
        assert data["schema_compliance_ratio"] > 0.50


@pytest.mark.asyncio
async def test_audit_dataset_contract_success() -> None:
    """Verify clean dataset passes Great Expectations contract checks."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Create preview
        prev_res = await client.post(
            "/api/v1/datasets/validate-preview",
            json={
                "filename": "clean_production_data.parquet",
                "file_format": "parquet",
                "raw_header": ["amount", "timestamp", "source_account_id", "destination_account_id", "channel_type", "is_fraud"],
                "sample_rows": [
                    {"amount": 540.0, "timestamp": "2026-09-01", "source_account_id": "a1b2c3d4e5f60718", "destination_account_id": "b2c3d4e5f60718a1", "channel_type": "WIRE", "is_fraud": 0}
                ],
                "total_bytes": 204800,
            },
        )
        preview_id = prev_res.json()["preview_id"]

        # 2. Run contract audit
        audit_res = await client.post(
            "/api/v1/datasets/contract-audit",
            json={
                "preview_id": preview_id,
                "bank_id": "bank_alpha",
                "column_mapping": {"amount": "transaction_amount"},
            },
        )
        assert audit_res.status_code == 200
        audit_data = audit_res.json()
        assert audit_data["status"] == "passed"
        assert audit_data["overall_compliance_score"] == 1.0
        assert audit_data["quarantined_records"] == 0
        assert len(audit_data["contract_checks"]) >= 4
        assert audit_data["dirichlet_alpha_estimate"] > 0


@pytest.mark.asyncio
async def test_audit_dataset_contract_quarantine_malformed() -> None:
    """Verify dataset with malformed/negative values triggers quarantine status."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Create preview with malformed filename indicator
        prev_res = await client.post(
            "/api/v1/datasets/validate-preview",
            json={
                "filename": "malformed_records_sample.csv",
                "file_format": "csv",
                "raw_header": ["amount", "timestamp", "is_fraud"],
                "sample_rows": [{"amount": -140.0, "timestamp": "2026-09-01", "is_fraud": 0}],
                "total_bytes": 5000,
            },
        )
        preview_id = prev_res.json()["preview_id"]

        # 2. Run contract audit
        audit_res = await client.post(
            "/api/v1/datasets/contract-audit",
            json={
                "preview_id": preview_id,
                "bank_id": "bank_beta",
                "column_mapping": {"amount": "transaction_amount"},
            },
        )
        assert audit_res.status_code == 200
        audit_data = audit_res.json()
        assert audit_data["status"] == "quarantined"
        assert audit_data["quarantined_records"] > 0
        assert audit_data["quarantine_csv_download_url"] is not None


@pytest.mark.asyncio
async def test_enroll_dataset_to_consortium() -> None:
    """Verify audited dataset enrolls into target bank partition."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        enroll_res = await client.post(
            "/api/v1/datasets/consortium-enroll",
            json={
                "audit_id": "AUD-TEST-9941",
                "target_bank_id": "bank_gamma",
                "allocation_mode": "replace_partition",
                "trigger_fl_round": True,
            },
        )
        assert enroll_res.status_code == 200
        enroll_data = enroll_res.json()
        assert enroll_data["bank_id"] == "bank_gamma"
        assert enroll_data["node_status"] == "ACTIVE_TRAINING"
        assert "bank_gamma_custom_partition" in enroll_data["partition_assigned"]
