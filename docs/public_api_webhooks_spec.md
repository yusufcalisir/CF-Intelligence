# 🔌 Public Integration API & Developer Webhooks Specification

The Collaborative Fraud Intelligence (CFI) Webhook Gateway enables core banking systems, payment processors, and case management suites to receive real-time cryptographically signed notifications for security events (`ALERT_CREATED`, `CASE_RESOLVED`, `MODEL_PROMOTED`, `DRIFT_DETECTED`).

All deliveries are protected by **HMAC-SHA256 signature verification**, **pre-registration and pre-flight Anti-SSRF validation**, and **fail-closed DNS resolution defenses**.

---

## 📌 1. Endpoint Overview

The Webhook Gateway exposes three REST endpoints mounted under `/v1/webhooks`:

| Endpoint | Method | Purpose | Security Controls |
| :--- | :---: | :--- | :--- |
| `/v1/webhooks/subscriptions` | `POST` | Register a new webhook target for an institution | Upfront SSRF URL validation, HTTPS enforcement, secret key generation |
| `/v1/webhooks/test-dispatch` | `POST` | Trigger a test signed payload to registered webhooks | Real-time signature generation, DNS-rebinding re-validation |
| `/v1/webhooks/verify` | `POST` | Utility endpoint to verify webhook signature authenticity | Constant-time HMAC comparison (`hmac.compare_digest`) |

---

## 📥 2. Request & Response Schemas

### 2.1 Register Webhook Subscription (`POST /v1/webhooks/subscriptions`)

**Request Payload:**
```json
{
  "tenant_id": "bank_alpha",
  "target_url": "https://api.bank-alpha.com/webhooks/cfi",
  "events": [
    "ALERT_CREATED",
    "CASE_RESOLVED"
  ]
}
```

**Response Payload (HTTP 200 OK):**
```json
{
  "subscription_id": "sub_a9f8b7c6",
  "tenant_id": "bank_alpha",
  "target_url": "https://api.bank-alpha.com/webhooks/cfi",
  "secret_key": "whsec_0123456789abcdef",
  "events": [
    "ALERT_CREATED",
    "CASE_RESOLVED"
  ]
}
```

> [!WARNING]
> Store the generated `secret_key` securely in your Key Management Vault (e.g., AWS KMS, HashiCorp Vault). The key is required to compute and verify the payload signature on every received webhook.

---

### 2.2 Trigger Test Dispatch (`POST /v1/webhooks/test-dispatch`)

**Query Parameters:**
- `tenant_id`: Target institution identifier (e.g. `bank_alpha`).
- `event_type`: Event category (e.g. `ALERT_CREATED`).

**Response Payload (HTTP 200 OK):**
```json
{
  "dispatched_count": 1,
  "event_type": "ALERT_CREATED",
  "sample_signature": "sha256=a8f5f167f44f4964e6c998dee827110c..."
}
```

---

### 2.3 Verify Incoming Webhook (`POST /v1/webhooks/verify`)

**Request Payload:**
```json
{
  "payload": {
    "test": true,
    "message": "CFI Simulator Webhook Dispatch Test",
    "sample_tx": "tx_test_1001"
  },
  "signature": "sha256=a8f5f167f44f4964e6c998dee827110c...",
  "secret_key": "whsec_0123456789abcdef"
}
```

**Response Payload (HTTP 200 OK):**
```json
{
  "valid": true
}
```

---

## 📢 3. Supported Webhook Event Types

| Event Type | Trigger Condition | Target Payload Content |
| :--- | :--- | :--- |
| **`ALERT_CREATED`** | A high-risk fraud anomaly exceeds the consortium threshold ($Score \ge 700$) | Anonymized transaction ID, risk score, triggered rule flags, and top feature attributions |
| **`CASE_RESOLVED`** | An investigation case is marked resolved by compliance analysts | Case ID, resolution outcome (`CONFIRMED_FRAUD` or `FALSE_POSITIVE`), and timestamp |
| **`MODEL_PROMOTED`** | A new global federated model surpasses champion benchmark metrics | Model version identifier, test PR-AUC, ROC-AUC, and timestamp |
| **`DRIFT_DETECTED`** | Data distribution shift auditor detects significant covariate or feature drift | Drifted feature names, Wasserstein distance ($W_1$), and JS-divergence metrics |

---

## 🛡️ 4. Anti-SSRF Defense Specification

The webhook engine strictly validates target URLs against Server-Side Request Forgery (SSRF) during subscription registration and prior to payload transmission:

1. **Protocol Restriction**: Only `http://` and `https://` schemes are permitted; non-web protocols (`file://`, `gopher://`, `ftp://`) are rejected immediately.
2. **Loopback & Localhost Blocking**: `127.0.0.0/8`, `::1`, `localhost`, `localhost.localdomain`, and `.local` / `.internal` suffixes.
3. **Cloud Metadata & Link-Local IP Blocking**:
   - `169.254.169.254` (AWS IMDSv1/v2, Azure, GCP metadata service)
   - `169.254.0.0/16` (IPv4 Link-Local)
   - `fe80::/10` (IPv6 Link-Local)
4. **Private RFC 1918 Subnets**:
   - `10.0.0.0/8`
   - `172.16.0.0/12`
   - `192.168.0.0/16`
5. **Reserved & Multicast Ranges**: `224.0.0.0/4`, `240.0.0.0/4`, `0.0.0.0/8`.
6. **DNS Resolution Check**: Resolves target hostnames via `socket.getaddrinfo` to identify hostnames resolving to internal or private addresses.
7. **Fail-Closed Resolution Policy**: If DNS resolution fails (`socket.gaierror`, `socket.herror`, `OSError`), the URL is rejected (`False`) to prevent unresolvable hostname SSRF bypasses.
8. **Double-Check DNS Rebinding Defense**: URL validation executes at registration time **AND** immediately before asynchronous delivery dispatch.

---

## 🔐 5. HMAC-SHA256 Signature Verification

Every outgoing HTTP POST request from the Webhook Gateway includes structured security headers:

```http
POST /webhooks/cfi HTTP/1.1
Host: api.bank-alpha.com
User-Agent: CF-Intelligence-Webhook/1.0
Content-Type: application/json
X-CFI-Event-Id: evt_99882211
X-CFI-Event-Type: ALERT_CREATED
X-CFI-Signature-256: sha256=a8f5f167f44f4964e6c998dee827110c...
X-CFI-Timestamp: 2026-09-14T12:00:00.000000+00:00

{
  "event_id": "evt_99882211",
  "event_type": "ALERT_CREATED",
  "payload": {
    "transaction_id": "tx_88992211",
    "risk_score": 895,
    "decision": "BLOCK"
  }
}
```

### 5.1 Verification Logic (Receiver-Side Python Example)

```python
import hmac
import hashlib

def verify_cfi_webhook(secret_key: str, payload_bytes: bytes, received_signature: str) -> bool:
    """Validate webhook payload HMAC-SHA256 signature in constant time.
    
    Args:
        secret_key: The subscription secret key (whsec_...) provided at registration.
        payload_bytes: The raw unparsed HTTP request body bytes.
        received_signature: Value from the X-CFI-Signature-256 header.
    
    Returns:
        True if the signature is valid, False otherwise.
    """
    expected_hex = hmac.new(
        secret_key.encode("utf-8"),
        payload_bytes,
        digestmod=hashlib.sha256,
    ).hexdigest()
    
    expected_signature = f"sha256={expected_hex}"
    
    # Constant-time comparison prevents timing side-channel attacks
    return hmac.compare_digest(expected_signature, received_signature)
```

Alternatively, partners can invoke `WebhookService.verify_signature(payload_bytes, received_signature, secret_key)` or call the API endpoint `POST /v1/webhooks/verify`.

---

## ⚡ 6. Asynchronous Delivery & SLA Semantics

- **Strict Delivery Timeout**: Outbound HTTP requests timeout after **3.0 seconds** (`httpx.AsyncClient(timeout=3.0)`).
- **Non-Blocking Execution**: Webhook deliveries execute asynchronously without blocking the core payment scoring or transaction ingestion pipeline.
- **Fail-Safe Logging**: Delivery failures and HTTP non-2xx status codes log structured failure events for SIEM/audit ingestion without interrupting ongoing federated rounds.

---

## 🧪 7. Automated Test Verification Matrix

All developer webhook capabilities, cryptographic signing routines, and SSRF defenses are verified by continuous automated test suites:

| Test Suite | File Path | Verified Capabilities | Status |
| :--- | :--- | :--- | :---: |
| **Subscription Registration** | `backend/tests/unit/test_webhook_gateway.py` | Registration, secret key generation (`whsec_`), event filtering | `PASSED` |
| **HMAC-SHA256 Signature** | `backend/tests/unit/test_webhook_gateway.py` | `compute_hmac_signature`, constant-time `hmac.compare_digest` | `PASSED` |
| **SSRF Perimeter Defense** | `backend/tests/unit/test_webhook_gateway.py` | Loopback, AWS metadata `169.254.169.254`, RFC 1918, non-HTTP scheme rejection | `PASSED` |
| **Fail-Closed DNS Validation** | `backend/tests/unit/test_webhook_gateway.py` | `socket.gaierror` fail-closed rejection, DNS-rebinding double-check | `PASSED` |
| **Verification Endpoint** | `backend/tests/unit/test_webhook_gateway.py` | `POST /v1/webhooks/verify` companion receiver validation | `PASSED` |
| **OpenAPI Contract Accuracy** | `backend/tests/unit/test_openapi_contract_accuracy.py` | Schema parity across all mounted routes including `/v1/webhooks` | `PASSED` |
