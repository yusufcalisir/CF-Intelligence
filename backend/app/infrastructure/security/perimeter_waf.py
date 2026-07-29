# ruff: noqa: UP042
"""Perimeter Web Application Firewall (WAF) Guard & OWASP Top 10 Security Engine."""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)


class WAFRuleCategory(str, Enum):
    """Categories of WAF security rules."""

    IP_WHITELIST = "IP_WHITELIST"
    SQLI_INJECTION = "SQLI_INJECTION"
    XSS_ATTACK = "XSS_ATTACK"
    SENSITIVE_PATH_BLOCKED = "SENSITIVE_PATH_BLOCKED"
    AUTH_LOCKOUT_EXCEEDED = "AUTH_LOCKOUT_EXCEEDED"
    NULL_BYTE_DETECTED = "NULL_BYTE_DETECTED"
    RATE_LIMIT_EXCEEDED = "RATE_LIMIT_EXCEEDED"


@dataclass
class WAFInspectionResult:
    """Dataclass holding WAF inspection verdict."""

    allowed: bool
    rule_triggered: WAFRuleCategory | None = None
    client_ip: str = ""
    reason: str = ""


class PerimeterWAFGuard:
    """Web Application Firewall inspecting incoming requests for malicious OWASP patterns and IP whitelists."""

    SQLI_PATTERNS = [
        re.compile(r"(\bUNION\b|\bSELECT\b|\bINSERT\b|\bDELETE\b|\bDROP\b)\s+.*", re.IGNORECASE),
        re.compile(r"(--|;\s*SHUTDOWN|;\s*DROP)", re.IGNORECASE),
        re.compile(r"'.*--", re.IGNORECASE),
    ]

    XSS_PATTERNS = [
        re.compile(r"<script.*?>.*?</script>", re.IGNORECASE),
        re.compile(r"javascript\s*:", re.IGNORECASE),
        re.compile(r"onload\s*=", re.IGNORECASE),
    ]

    SENSITIVE_PATHS = [
        "/.env",
        "/admin",
        "/actuator",
        "/.git",
        "/wp-admin",
        "/config.json",
    ]

    def __init__(
        self,
        whitelisted_ips: list[str] | None = None,
        enforce_whitelist: bool = False,
        max_auth_failures: int = 5,
        lockout_duration_seconds: int = 300,
    ) -> None:
        self.whitelisted_ips = set(whitelisted_ips or ["127.0.0.1", "10.0.0.1"])
        self.enforce_whitelist = enforce_whitelist
        self.max_auth_failures = max_auth_failures
        self.lockout_duration_seconds = lockout_duration_seconds

        # Client IP failure tracking: client_ip -> list of failure timestamps
        self._auth_failures: dict[str, list[float]] = {}
        self._request_counts: dict[str, int] = {}

    def add_whitelisted_ip(self, ip_address: str) -> None:
        """Adds an IP address to the perimeter whitelist."""
        self.whitelisted_ips.add(ip_address)
        logger.info("Added IP %s to WAF whitelist.", ip_address)

    def record_failed_auth_attempt(self, client_ip: str) -> bool:
        """Records a failed auth attempt for client IP. Returns True if account is now locked out."""
        now = time.time()
        if client_ip not in self._auth_failures:
            self._auth_failures[client_ip] = []

        # Remove failures older than lockout_duration_seconds
        self._auth_failures[client_ip] = [
            t for t in self._auth_failures[client_ip]
            if now - t <= self.lockout_duration_seconds
        ]
        self._auth_failures[client_ip].append(now)

        is_locked = len(self._auth_failures[client_ip]) >= self.max_auth_failures
        if is_locked:
            logger.warning("WAF LOCKOUT ACTIVE for IP %s (%d failed attempts).", client_ip, len(self._auth_failures[client_ip]))
        return is_locked

    def is_client_locked_out(self, client_ip: str) -> bool:
        """Checks if client IP is currently locked out due to consecutive auth failures."""
        now = time.time()
        failures = [
            t for t in self._auth_failures.get(client_ip, [])
            if now - t <= self.lockout_duration_seconds
        ]
        return len(failures) >= self.max_auth_failures

    def validate_tenant_access(self, header_bank_id: str | None, jwt_bank_id: str | None) -> bool:
        """A01: Broken Access Control check ensuring X-Bank-ID header matches authenticated JWT claim."""
        if not header_bank_id or not jwt_bank_id:
            return False
        return header_bank_id.lower().strip() == jwt_bank_id.lower().strip()

    def get_secure_response_headers(self) -> dict[str, str]:
        """A02: Cryptographic Failures mitigation returning secure HTTP response headers."""
        return {
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "X-Content-Type-Options": "nosniff",
            "X-Frame-Options": "DENY",
            "X-XSS-Protection": "1; mode=block",
        }

    def inspect_request(
        self,
        client_ip: str,
        path: str = "",
        headers: dict[str, str] | None = None,
        body: str = "",
    ) -> WAFInspectionResult:
        """Inspects request path, body, and headers against OWASP Top 10 WAF rule sets."""
        headers = headers or {}

        # 1. IP Whitelist check
        if self.enforce_whitelist and client_ip not in self.whitelisted_ips:
            logger.warning("WAF BLOCKED IP %s: Not in IP whitelist.", client_ip)
            return WAFInspectionResult(
                allowed=False,
                rule_triggered=WAFRuleCategory.IP_WHITELIST,
                client_ip=client_ip,
                reason="Client IP address not whitelisted.",
            )

        # 2. A07: Auth Lockout Check
        if self.is_client_locked_out(client_ip):
            logger.warning("WAF BLOCKED IP %s: Exceeded 5 failed auth attempts.", client_ip)
            return WAFInspectionResult(
                allowed=False,
                rule_triggered=WAFRuleCategory.AUTH_LOCKOUT_EXCEEDED,
                client_ip=client_ip,
                reason="Account locked out after 5 consecutive auth failures.",
            )

        # 3. A05: Sensitive Path Inspection
        path_lower = path.lower()
        if any(path_lower.startswith(p) or path_lower == p for p in self.SENSITIVE_PATHS):
            logger.warning("WAF BLOCKED IP %s: Sensitive path access blocked (%s).", client_ip, path)
            return WAFInspectionResult(
                allowed=False,
                rule_triggered=WAFRuleCategory.SENSITIVE_PATH_BLOCKED,
                client_ip=client_ip,
                reason=f"Access to sensitive path {path} blocked.",
            )

        # 4. A03: Null Byte Detection
        if "\x00" in body or "\x00" in path:
            logger.warning("WAF BLOCKED IP %s: Null byte injection detected.", client_ip)
            return WAFInspectionResult(
                allowed=False,
                rule_triggered=WAFRuleCategory.NULL_BYTE_DETECTED,
                client_ip=client_ip,
                reason="Null byte injection attempt detected.",
            )

        # 5. A03: SQL Injection check
        for pattern in self.SQLI_PATTERNS:
            if pattern.search(body) or pattern.search(path):
                logger.warning("WAF BLOCKED IP %s: SQL Injection pattern detected.", client_ip)
                return WAFInspectionResult(
                    allowed=False,
                    rule_triggered=WAFRuleCategory.SQLI_INJECTION,
                    client_ip=client_ip,
                    reason="Malicious SQL Injection pattern detected.",
                )

        # 6. Cross-Site Scripting (XSS) check
        for pattern in self.XSS_PATTERNS:
            if pattern.search(body) or pattern.search(path):
                logger.warning("WAF BLOCKED IP %s: XSS pattern detected.", client_ip)
                return WAFInspectionResult(
                    allowed=False,
                    rule_triggered=WAFRuleCategory.XSS_ATTACK,
                    client_ip=client_ip,
                    reason="Malicious XSS payload detected.",
                )

        return WAFInspectionResult(allowed=True, client_ip=client_ip)
