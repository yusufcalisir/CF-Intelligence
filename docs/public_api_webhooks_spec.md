# 🔌 Public Integration API & Developer Webhooks Specification

The Webhook Gateway allows financial institutions and developer partners to subscribe to real-time notification events (`ALERT_CREATED`, `CASE_RESOLVED`, `MODEL_PROMOTED`, `DRIFT_DETECTED`) delivered with HMAC-SHA256 signature verification and automated SSRF protection.

---

## 📌 Endpoint Overview

- **Subscription Endpoint**: `POST /v1/webhooks/subscriptions` (Enforces real-time SSRF validation upfront)
- **Test Dispatch Endpoint**: `POST /v1/webhooks/test-dispatch` (Delivers signed payload with DNS-rebinding SSRF verification)
- **Receiver Verification Endpoint**: `POST /v1/webhooks/verify` (Receiver-side HMAC-SHA256 verification using constant-time `hmac.compare_digest`)

---

## 🛡️ Anti-SSRF Defense Specification

The webhook engine strictly validates target URLs against server-side request forgery (SSRF) during subscription registration and prior to payload transmission:
- **Loopback Blocking**: `127.0.0.0/8`, `::1`, `localhost`
- **Cloud Metadata & Link-Local**: `169.254.169.254` (AWS/Azure/GCP metadata), `169.254.0.0/16`, `fe80::/10`
- **Private RFC 1918 Subnets**: `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`
- **Reserved & Multicast**: `224.0.0.0/4`, `240.0.0.0/4`
- **DNS Resolution Check**: Resolves target hostnames via `socket.getaddrinfo` to catch hostnames mapped to internal IPs.
- **Fail-Closed Resolution Policy**: If DNS resolution fails (`socket.gaierror`, `socket.herror`, `OSError`), the URL is strictly rejected (`False`) to prevent unresolvable hostname SSRF bypasses.
- **Double Check (DNS Rebinding Defense)**: URL validation runs at registration time AND immediately before async delivery dispatch.

---

## 🔐 HMAC-SHA256 Signature Verification

Every outgoing HTTP POST request from the Webhook Gateway includes an `X-CFI-Signature-256` header:

```http
POST /webhooks/cfi HTTP/1.1
Host: api.bank-alpha.com
X-CFI-Signature-256: sha256=a8f5f167f44f4964e6c998dee827110c...
Content-Type: application/json

{
  "event_id": "evt_99882211",
  "event_type": "ALERT_CREATED",
  "payload": { ... }
}
```

### Verification Logic (Receiver-side Python)

```python
import hmac
import hashlib

def verify_webhook(secret_key: str, payload_bytes: bytes, received_sig: str) -> bool:
    """Validate webhook payload HMAC-SHA256 signature in constant time."""
    expected_hex = hmac.new(
        secret_key.encode("utf-8"),
        payload_bytes,
        digestmod=hashlib.sha256
    ).hexdigest()
    expected_sig = f"sha256={expected_hex}"
    return hmac.compare_digest(expected_sig, received_sig)
```

Alternatively, partners can invoke `WebhookService.verify_signature(payload_bytes, received_signature, secret_key)` or call `POST /v1/webhooks/verify`.

