import pytest
from httpx import AsyncClient, ASGITransport
from app.main import app

@pytest.mark.asyncio
async def test_api_routes_strict_security_headers():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/health")
        assert resp.status_code == 200
        csp = resp.headers.get("Content-Security-Policy", "")
        assert "default-src 'none'" in csp
        assert "script-src 'none'" in csp
        assert resp.headers.get("X-Frame-Options") == "DENY"
        assert resp.headers.get("X-Content-Type-Options") == "nosniff"

@pytest.mark.asyncio
async def test_docs_and_scalar_permissive_csp_for_swagger_cdn():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Test /scalar endpoint
        resp_scalar = await client.get("/scalar")
        assert resp_scalar.status_code == 200
        csp_scalar = resp_scalar.headers.get("Content-Security-Policy", "")
        assert "https://cdn.jsdelivr.net" in csp_scalar
        assert "script-src" in csp_scalar
        assert "'unsafe-inline'" in csp_scalar

        # Test /docs endpoint
        resp_docs = await client.get("/docs")
        assert resp_docs.status_code == 200
        csp_docs = resp_docs.headers.get("Content-Security-Policy", "")
        assert "https://cdn.jsdelivr.net" in csp_docs
        assert "'unsafe-inline'" in csp_docs
