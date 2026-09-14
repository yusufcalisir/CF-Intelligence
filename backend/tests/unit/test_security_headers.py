# ruff: noqa: E402
import sys
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

root_dir = Path(__file__).resolve().parents[3]
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

from app.infrastructure.security.perimeter_waf import (
    PerimeterWAFGuard,
    WAFRuleCategory,
)
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
async def test_comprehensive_http_security_headers():
    """Verify all 7 standard defensive HTTP response headers are present on API responses."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/health")
        assert resp.status_code == 200

        headers = resp.headers
        assert "Strict-Transport-Security" in headers
        assert "max-age=31536000" in headers["Strict-Transport-Security"]
        assert "includeSubDomains" in headers["Strict-Transport-Security"]
        assert headers.get("X-Content-Type-Options") == "nosniff"
        assert headers.get("X-Frame-Options") == "DENY"
        assert headers.get("Referrer-Policy") == "no-referrer"
        assert "Permissions-Policy" in headers
        assert "geolocation=()" in headers["Permissions-Policy"]
        assert headers.get("X-XSS-Protection") == "1; mode=block"


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


# ── Perimeter WAF Header Inspection & Memory Pruning Tests ────────────────────


def test_perimeter_waf_header_sqli_inspection():
    """Verify WAF inspects request headers and blocks header-based SQL injection."""
    waf = PerimeterWAFGuard()
    malicious_headers = {"X-Filter": "UNION SELECT * FROM accounts;--"}
    result = waf.inspect_request(
        client_ip="192.168.1.50",
        path="/api/v1/predict",
        headers=malicious_headers,
        body="{}",
    )
    assert result.allowed is False
    assert result.rule_triggered == WAFRuleCategory.SQLI_INJECTION


def test_perimeter_waf_header_xss_inspection():
    """Verify WAF inspects request headers and blocks header-based XSS attacks."""
    waf = PerimeterWAFGuard()
    malicious_headers = {"User-Agent": "Mozilla/5.0 <script>alert(document.cookie)</script>"}
    result = waf.inspect_request(
        client_ip="192.168.1.51",
        path="/api/v1/alerts",
        headers=malicious_headers,
        body="{}",
    )
    assert result.allowed is False
    assert result.rule_triggered == WAFRuleCategory.XSS_ATTACK


def test_perimeter_waf_header_null_byte_inspection():
    """Verify WAF inspects request headers and blocks null bytes in header values."""
    waf = PerimeterWAFGuard()
    malicious_headers = {"X-Custom-Tenant": "bank_a\x00_bypass"}
    result = waf.inspect_request(
        client_ip="192.168.1.52",
        path="/api/v1/cases",
        headers=malicious_headers,
        body="{}",
    )
    assert result.allowed is False
    assert result.rule_triggered == WAFRuleCategory.NULL_BYTE_DETECTED


def test_perimeter_waf_memory_pruning():
    """Verify WAF prunes expired IP tracking entries when size exceeds max_tracked_ips."""
    import time

    waf = PerimeterWAFGuard(max_tracked_ips=10, lockout_duration_seconds=5)
    now = time.time()
    expired_time = now - 10.0

    # Fill internal tracking with 15 expired IPs
    for i in range(15):
        waf._auth_failures[f"192.168.10.{i}"] = [expired_time]

    assert len(waf._auth_failures) == 15

    # Trigger record_failed_auth_attempt for a new active IP
    waf.record_failed_auth_attempt("10.0.0.99")

    # Expired entries should be pruned, retaining only active IP
    assert len(waf._auth_failures) == 1
    assert "10.0.0.99" in waf._auth_failures


def test_perimeter_waf_lockout_reset():
    """Verify WAF resets client lockout upon explicit reset_client_lockout invocation."""
    waf = PerimeterWAFGuard(max_auth_failures=3)
    test_ip = "192.168.20.5"

    for _ in range(3):
        waf.record_failed_auth_attempt(test_ip)

    assert waf.is_client_locked_out(test_ip) is True

    # Reset lockout
    waf.reset_client_lockout(test_ip)
    assert waf.is_client_locked_out(test_ip) is False


def test_perimeter_waf_concurrency_thread_safety():
    """Verify concurrent failed auth recordings across multiple threads maintain exact count."""
    import concurrent.futures

    waf = PerimeterWAFGuard(max_auth_failures=100)
    test_ip = "192.168.30.10"

    def record():
        waf.record_failed_auth_attempt(test_ip)

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(record) for _ in range(50)]
        concurrent.futures.wait(futures)

    assert len(waf._auth_failures[test_ip]) == 50


# ── Cloudflare WAF Setup Script Tests ──────────────────────────────────────────


def test_cloudflare_waf_setup_script_api_calls(monkeypatch):
    """Verify Cloudflare WAF setup script configures settings, rules, and rate limits."""
    from scripts.setup_cloudflare_waf import (
        configure_rate_limiting_rules,
        configure_security_settings,
        configure_waf_rules,
    )

    recorded_calls: list[dict] = []

    def mock_cf_request(endpoint: str, method: str = "GET", token: str = "", data: dict | None = None):
        recorded_calls.append({"endpoint": endpoint, "method": method, "data": data})
        return {"success": True, "result": {}}

    monkeypatch.setattr("scripts.setup_cloudflare_waf.cf_request", mock_cf_request)

    configure_security_settings("zone_123", "token_abc")
    configure_waf_rules("zone_123", "token_abc")
    configure_rate_limiting_rules("zone_123", "token_abc")

    # Verify settings calls were made (8 settings)
    setting_calls = [c for c in recorded_calls if "settings" in c["endpoint"]]
    assert len(setting_calls) == 8

    # Verify WAF ruleset call was made
    waf_calls = [c for c in recorded_calls if "rulesets" in c["endpoint"]]
    assert len(waf_calls) == 1
    assert waf_calls[0]["method"] == "PUT"
    assert len(waf_calls[0]["data"]["rules"]) >= 2

    # Verify rate limits call was made
    rate_calls = [c for c in recorded_calls if "rate_limits" in c["endpoint"]]
    assert len(rate_calls) == 1
    assert rate_calls[0]["method"] == "POST"
    assert rate_calls[0]["data"]["threshold"] == 100
