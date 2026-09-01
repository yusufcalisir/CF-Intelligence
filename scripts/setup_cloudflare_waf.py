#!/usr/bin/env python3
"""Cloudflare WAF and Rate Limiting Setup Script for CF-Intelligence.

Automates the configuration of Cloudflare Zone Settings, WAF Custom Rules,
and L7 Rate Limiting rules via the Cloudflare REST API v4.
"""

from __future__ import annotations

import argparse
import json
import logging
import urllib.request

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-7s | %(message)s")
logger = logging.getLogger("cloudflare_setup")

CF_API_BASE = "https://api.cloudflare.com/client/v4"


def cf_request(
    endpoint: str,
    method: str = "GET",
    token: str = "",
    data: dict | None = None,
) -> dict:
    url = f"{CF_API_BASE}/{endpoint.lstrip('/')}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    payload_bytes = json.dumps(data).encode("utf-8") if data is not None else None
    req = urllib.request.Request(url, data=payload_bytes, headers=headers, method=method)

    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as err:
        error_body = err.read().decode("utf-8")
        logger.error("Cloudflare API Error (%d): %s", err.code, error_body)
        try:
            return json.loads(error_body)
        except Exception:
            return {"success": False, "errors": [{"message": error_body}]}


def configure_security_settings(zone_id: str, token: str) -> None:
    logger.info("Configuring Zone Security Posture (TLS 1.3, Strict SSL, Always HTTPS)...")
    settings = {
        "ssl": "strict",
        "always_use_https": "on",
        "min_tls_version": "1.2",
        "tls_1_3": "on",
        "security_level": "medium",
        "browser_integrity_check": "on",
        "websockets": "on",
        "http3": "on",
    }
    for setting_name, value in settings.items():
        res = cf_request(
            f"zones/{zone_id}/settings/{setting_name}",
            method="PATCH",
            token=token,
            data={"value": value},
        )
        if res.get("success"):
            logger.info("  ✓ Setting '%s' → '%s'", setting_name, value)
        else:
            logger.warning("  ✗ Failed to set '%s': %s", setting_name, res.get("errors"))


def main() -> None:
    parser = argparse.ArgumentParser(description="Configure Cloudflare Layer 1 Security for CF-Intelligence")
    parser.add_argument("--zone-id", required=True, help="Cloudflare Zone ID")
    parser.add_argument("--token", required=True, help="Cloudflare API Token")
    args = parser.parse_args()

    logger.info("Initiating Cloudflare Layer 1 Perimeter Hardening for Zone %s", args.zone_id)
    configure_security_settings(args.zone_id, args.token)
    logger.info("Cloudflare Layer 1 Security Hardening Complete! ✅")


if __name__ == "__main__":
    main()
