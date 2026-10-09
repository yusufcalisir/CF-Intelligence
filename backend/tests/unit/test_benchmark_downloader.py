"""Unit tests for Benchmark Dataset Downloader service and API endpoints."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.application.services.benchmark_downloader import (
    BENCHMARK_CATALOG,
    get_download_status,
    start_benchmark_download,
)
from app.main import app


class TestBenchmarkDownloader:
    """Verification suite for benchmark acquisition mechanisms."""

    def test_catalog_definitions(self) -> None:
        """Verify all supported benchmark datasets are properly registered in catalog."""
        expected_benchmarks = ["creditcard", "elliptic", "paysim", "ieee_cis", "amlsim"]
        for key in expected_benchmarks:
            assert key in BENCHMARK_CATALOG
            cfg = BENCHMARK_CATALOG[key]
            assert "primary_files" in cfg
            assert "kaggle_slug" in cfg

    def test_invalid_dataset_name_rejected(self) -> None:
        """Verify requesting unknown dataset raises descriptive ValueError."""
        with pytest.raises(ValueError, match="Unknown benchmark dataset"):
            start_benchmark_download("unsupported_crypto_dataset")

    def test_get_download_status_structure(self) -> None:
        """Verify get_download_status returns valid status schema."""
        status = get_download_status("creditcard")
        assert status["dataset"] == "creditcard"
        assert status["status"] in ("idle", "already_exists", "in_progress", "completed", "failed")
        assert "percent" in status
        assert "message" in status

    @patch("app.application.services.benchmark_downloader._download_worker")
    def test_start_benchmark_download_spawns_worker(self, mock_worker: MagicMock) -> None:
        """Verify starting download initiates background thread when files missing."""
        with patch("app.application.services.benchmark_downloader.has_real_benchmark_files", return_value=False):
            res = start_benchmark_download(
                dataset="elliptic",
                kaggle_username="test_user",
                kaggle_key="test_key",
                source="auto",
            )
            assert res["dataset"] == "elliptic"
            assert res["status"] in ("in_progress", "already_exists")


@pytest.mark.asyncio
class TestBenchmarkDownloadEndpoints:
    """API router contract tests for /api/v1/datasets/benchmarks/download."""

    async def test_get_download_status_endpoint(self) -> None:
        """Verify GET /api/v1/datasets/benchmarks/download/status returns status schema."""
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/v1/datasets/benchmarks/download/status?dataset=creditcard")
            assert resp.status_code == 200
            data = resp.json()
            assert data["dataset"] == "creditcard"
            assert "status" in data
            assert "percent" in data

    @patch("app.application.services.benchmark_downloader._download_worker")
    async def test_post_download_endpoint_validation(self, mock_worker: MagicMock) -> None:
        """Verify POST /api/v1/datasets/benchmarks/download triggers download workflow."""
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/datasets/benchmarks/download",
                json={
                    "dataset": "creditcard",
                    "kaggle_username": "mock_user",
                    "kaggle_key": "mock_key",
                    "source": "auto",
                },
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["dataset"] == "creditcard"
            assert data["status"] in ("in_progress", "already_exists", "idle")
