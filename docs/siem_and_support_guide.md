# 📊 SIEM Integration & Support Diagnostic Bundle Guide

The Collaborative Fraud Intelligence (CFI) platform delivers enterprise security logging via `SIEMLogExporter` and sanitized technical diagnostics via `SupportDiagnosticCompiler`.

---

## 📌 SIEM Log Exporter Architecture (`SIEMLogExporter`)

```
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                          SECURITY EVENT EXPORTING PIPELINE                             │
│                                                                                        │
│  [ Platform Security & Audit Events ]                                                  │
│   - Byzantine Gradient Poisoning Detected                                              │
│   - Model Promoted to Active Production                                                │
│   - Four-Eyes Case Dual-Signed & Closed                                                │
│   - KMS Data Key Rotated & Re-encrypted                                                │
│   - WAF Rule Triggered (SQLi / XSS / BOLA)                                             │
│               │                                                                        │
│               ▼                                                                        │
│  ┌──────────────────────────────────────────────────────────────────────────────────┐  │
│  │                              SIEMLogExporter Engine                              │  │
│  │                                                                                  │  │
│  │   ├── Format 1: ArcSight / QRadar Common Event Format (CEF Syslog)                │  │
│  │   ├── Format 2: IETF RFC 5424 Structured Syslog (UDP / TLS)                      │  │
│  │   ├── Format 3: Splunk HTTP Event Collector (HEC)                                │  │
│  │   └── Format 4: Datadog / Elastic Common Schema (ECS) JSON                       │  │
│  └──────────────────────────────────────────────────────────────────────────────────┘  │
│               │                                                                        │
│               ├───► Splunk Enterprise / Splunk Cloud (:8088/services/collector/event) │
│               ├───► IBM QRadar / Micro Focus ArcSight (Syslog UDP/TCP)                 │
│               └───► Datadog Logs API (v2 /api/v2/logs)                                 │
└────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 📑 Supported SIEM Formats & Payloads

### 1. ArcSight Common Event Format (CEF)
```syslog
CEF:0|CFI|Simulator|2.0|MODEL_PROMOTED|Model version model_v2.0.0 promoted to active champion.|10|eventId=evt_889900 srcBank=bank_alpha rt=2026-09-07T12:00:00Z
```

### 2. RFC 5424 Structured Syslog
Conforms strictly to IETF RFC 5424 with priority `<134>1` (Facility 16 `local0`, Severity 6 `Informational`):
```syslog
<134>1 2026-09-07T12:00:00.000000Z cfi-gateway CFI - - - {"event": "GRADIENT_SUBMITTED", "bank_id": "bank_alpha", "round_id": 47}
```

### 3. Splunk HTTP Event Collector (HEC)
Dispatched via authenticated POST to `https://<splunk_host>:8088/services/collector/event`:
```json
{
  "time": 1788782400.0,
  "host": "cfi-coordinator-prod-01",
  "source": "cfi_simulator",
  "sourcetype": "cfi:audit:json",
  "event": {
    "event_id": "evt_991200",
    "event_type": "BYZANTINE_ATTACK_DEFENSE",
    "severity": "CRITICAL",
    "bank_id": "bank_gamma",
    "action": "QUARANTINED_BY_KRUM",
    "gradient_distance": 48.2,
    "threshold": 14.1
  }
}
```

### 4. Datadog JSON
```json
{
  "ddsource": "cfi_simulator",
  "service": "cfi_control_plane",
  "hostname": "cfi-pod-88",
  "status": "warning",
  "event_id": "evt_889900",
  "event_type": "KMS_KEY_ROTATED",
  "tenant_id": "bank_beta",
  "message": "Rotated AES-256-GCM data key from v1 to v2. Re-encrypted 1,420 rows."
}
```

---

## 🩺 Support Diagnostic Compiler (`SupportDiagnosticCompiler`)

When financial institutions open a support case, the `SupportDiagnosticCompiler` generates an encrypted, PII-redacted technical diagnostics bundle.

### Zero-PII Sanitization Rules
The compiler scans all log files, environment dumps, and stack traces:
- **Email Addresses**: Converted to `[REDACTED_EMAIL]`.
- **IBAN / Account Numbers**: Redacted to `[REDACTED_IBAN]` preserving only country prefix.
- **Credit Card PANs**: Redacted to `[REDACTED_PAN]` (masked via Luhn validator).
- **Session Tokens / Keys**: Bearer tokens, JWTs, and API keys are zeroed out.

### Diagnostic Bundle Manifest
```json
{
  "diagnostic_id": "diag_3399ab",
  "generated_at": "2026-09-07T12:05:00Z",
  "sanitization_status": "PII_REDACTED_PASSED",
  "included_logs": ["gateway.log", "fl_engine.log", "migration_check.log"],
  "sha256_fingerprint": "a3f5b7...99c2"
}
```

---

## 🧪 Automated Unit Test Suite

```bash
pytest backend/tests/unit/test_siem_support_diagnostics.py -v
```

**Verification Results:**
- `test_siem_cef_and_json_formatting`: `PASSED` (CEF, Splunk, Datadog schemas verified)
- `test_syslog_format_is_valid_rfc5424`: `PASSED` (UDP socket capture validates `<134>1` envelope)
- `test_splunk_payload_structure`: `PASSED` (Verifies HEC URL, bearer token, and JSON event body)
- `test_support_diagnostic_pii_redaction`: `PASSED` (Verifies zero PII leakage in diagnostic bundle)
