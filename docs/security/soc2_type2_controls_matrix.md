# SOC 2 Type II Controls Matrix & Automated Evidence Verification (Pre-Audit Readiness)

This document defines the SOC 2 Type II Trust Services Criteria (TSC) security controls implemented across the Collaborative Fraud Intelligence (CFI) Platform, detailing automated verification endpoints, implementation modules, and unit test proofs for continuous compliance auditing.

> [!NOTE]
> **Audit-Readiness Notice:**  
> These automated checks and control matrices represent **internal control design and automated continuous evidence collection pipelines** (pre-audit readiness state). Formal SOC 2 Type II certification requires an independent examination and opinion issued by an accredited AICPA third-party audit firm evaluating operational effectiveness over a minimum 6-month observation period.

---

## SOC 2 Trust Services Criteria (TSC) Matrix

The table below maps the 2017 AICPA Trust Services Criteria (Common Criteria series) to platform implementations and automated test proofs:

| Control ID | AICPA Category | Control Title | Technical Implementation Description | Implementation Module | Automated Verification Proof | Status |
| :--- | :--- | :--- | :--- | :--- | :--- | :---: |
| **CC6.1** | Logical Access | Logical Access Controls & Route Authentication | All non-public API endpoints mandate OAuth2 JWT bearer tokens or X.509 mutual TLS (mTLS) client certificates. Unauthenticated requests are rejected with `401 Unauthorized`. | [compliance.py](../../backend/app/presentation/routers/compliance.py)<br>[security.py](../../backend/app/presentation/routers/security.py) | `POST /v1/compliance/soc2-evidence`<br>[test_cc6_1_all_endpoints_authenticated()](../../backend/tests/unit/test_security_controls_audit.py) | `PASS` |
| **CC6.2** | Transmission | Data Transmission Encryption (TLS 1.3 / SSL) | All external HTTP/gRPC ingress, inter-microservice federation channels (`net-federation`), and PostgreSQL database connections mandate TLS encryption (`sslmode=require`). | [database/__init__.py](../../backend/app/infrastructure/database/__init__.py)<br>[security_compliance.py](../../backend/app/application/services/security_compliance.py) | `POST /v1/compliance/soc2-evidence`<br>`DATABASE_URL` TLS enforcement test in `generate_soc2_evidence_report()` | `PASS` |
| **CC6.3** | Access Control | Secrets Management & Vault/KMS Envelope Encryption | Sensitive credentials, RSA private keys, and API tokens are managed through HashiCorp Vault PKI / KV v2 or AWS KMS envelope encryption. Zero plaintext secrets exist in production environment variables. | [kms_service.py](../../backend/app/application/services/kms_service.py)<br>[security_compliance.py](../../backend/app/application/services/security_compliance.py) | `POST /v1/compliance/soc2-evidence`<br>[test_cc6_3_no_secrets_in_env()](../../backend/tests/unit/test_security_controls_audit.py) | `PASS` |
| **CC6.6** | Perimeter Defense | Perimeter WAF Protection & Attack Payload Rejection | Edge gateway is protected by [PerimeterWAFGuard](../../backend/app/infrastructure/security/perimeter_waf.py), enforcing IP whitelisting, thread-safe brute-force lockout with bounded memory pruning (5 failures / 300s, max 1000 IPs), sensitive path blocking, and rejection of SQL injection, XSS, and null-byte payloads across URL paths, JSON bodies, and HTTP headers. | [perimeter_waf.py](../../backend/app/infrastructure/security/perimeter_waf.py) | [test_sql_injection_rejected()](../../backend/tests/unit/test_security_controls_audit.py)<br>[test_env_file_not_exposed()](../../backend/tests/unit/test_security_controls_audit.py)<br>[test_auth_lockout_after_5_failures()](../../backend/tests/unit/test_security_controls_audit.py)<br>[test_perimeter_waf_header_sqli_inspection()](../../backend/tests/unit/test_security_headers.py) | `PASS` |
| **CC7.1** | Monitoring | Immutable Cryptographic Audit Logging | Sequential SHA-256 block hash chaining binds all data access events, fraud case state transitions, and consortium governance actions in an append-only cryptographic audit chain. | [immutable_audit_chain.py](../../backend/app/infrastructure/security/immutable_audit_chain.py)<br>[security_compliance.py](../../backend/app/application/services/security_compliance.py) | `POST /v1/compliance/soc2-evidence`<br>[test_cc7_1_audit_log_has_entries()](../../backend/tests/unit/test_security_controls_audit.py) | `PASS` |
| **CC8.1** | Change Management | CI/CD Automated Change Management & Security Scanning | Code changes mandate automated GitHub Actions CI pipeline execution enforcing pytest unit test suite, ruff code formatting/linting, and Bandit SAST security scans before merge approval into `main`. | `.github/workflows/ci.yml`<br>[security_compliance.py](../../backend/app/application/services/security_compliance.py) | `POST /v1/compliance/soc2-evidence`<br>GitHub Actions workflow status & `test_generate_soc2_evidence_report()` | `PASS` |
| **CC9.1** | Risk Mitigation | Vendor Risk & Dependency Version Pinning | Third-party dependencies are pinned with strict version constraints in [pyproject.toml](../../backend/pyproject.toml) and SHA-512 integrity hashes in `frontend/package-lock.json`, mitigating supply-chain vulnerabilities. | [pyproject.toml](../../backend/pyproject.toml)<br>[security_compliance.py](../../backend/app/application/services/security_compliance.py) | `POST /v1/compliance/soc2-evidence`<br>[test_generate_soc2_evidence_report()](../../backend/tests/unit/test_security_controls_audit.py) | `PASS` |

---

## Perimeter WAF & Edge Security Controls (CC6.6 Deep-Dive)

The [PerimeterWAFGuard](../../backend/app/infrastructure/security/perimeter_waf.py) module provides real-time defense against OWASP Top 10 vulnerabilities:

1. **SQL Injection (SQLi) Defense**: Inspects incoming URL paths, request bodies, and HTTP headers for regex patterns (`UNION SELECT`, `DROP TABLE`, `OR 1=1`, stacked queries). Malicious requests are rejected immediately with `400 Bad Request`.
2. **Cross-Site Scripting (XSS) Defense**: Scans request payloads and headers for script execution patterns (`<script>`, `javascript:`, `onload=`), rejecting with `400 Bad Request`.
3. **Sensitive Path Inspection**: Blocks directory scanning and unauthorized path access attempts (`/.env`, `/admin`, `/actuator`, `/.git`, `/wp-admin`, `/config.json`) with `403 Forbidden`.
4. **Null-Byte Injection Rejection**: Intercepts `\x00` null bytes in paths, JSON bodies, or HTTP headers to prevent byte-poisoning attacks.
5. **Brute Force & Auth Lockout (A07)**: Enforces thread-safe atomic lockout (`threading.Lock`) for any client IP accumulating $\ge 5$ consecutive authentication failures within a 300-second sliding window. Automatic LRU pruning bounds tracked records to 1,000 IPs, eliminating memory exhaustion.
6. **CORS Security**: Disallows wildcard (`*`) access control origins; strictly validates cross-origin requests against explicit domain whitelists (`https://cf-intelligence.vercel.app`, `http://localhost:5173`).

---

## Automated SOC 2 Evidence API

Auditors, security officers, and compliance monitoring agents can query real-time evidence reports programmatically through the compliance router in [compliance.py](../../backend/app/presentation/routers/compliance.py):

### Request Specifications
Both `GET` and `POST` methods are supported:

```http
POST /v1/compliance/soc2-evidence HTTP/1.1
Host: api.cfi-platform.org
Authorization: Bearer <AUDITOR_JWT_TOKEN>
Content-Type: application/json
```

```http
GET /api/v1/compliance/soc2-evidence HTTP/1.1
Host: api.cfi-platform.org
Authorization: Bearer <AUDITOR_JWT_TOKEN>
```

### Sample Response (`200 OK`)

```json
{
  "report_id": "soc2_evidence_20260914_142500",
  "timestamp": "2026-09-14T14:25:00.123456Z",
  "compliance_status": "COMPLIANT",
  "total_controls_audited": 6,
  "passed_controls": 6,
  "failed_controls": 0,
  "controls": {
    "CC6.1": {
      "title": "Logical Access Controls & Route Authentication",
      "status": "PASS",
      "evidence": "All production API routes protected via OAuth2 JWT or mTLS certificates."
    },
    "CC6.2": {
      "title": "Data Transmission Encryption (TLS/SSL)",
      "status": "PASS",
      "evidence": "Database URL specifies encrypted TLS transport (localhost:5432/cfi_db?sslmode=require)."
    },
    "CC6.3": {
      "title": "Secrets Management & Vault/KMS Envelope Encryption",
      "status": "PASS",
      "evidence": "All credentials managed via Vault PKI or AWS KMS envelope encryption (0 checked). Inspected 48 env vars."
    },
    "CC7.1": {
      "title": "Immutable Cryptographic Audit Logging",
      "status": "PASS",
      "evidence": "SHA-256 tamper-evident cryptographic audit logging chain active; all data access events logged."
    },
    "CC8.1": {
      "title": "CI/CD Automated Change Management & Security Scanning",
      "status": "PASS",
      "evidence": "GitHub Actions CI pipeline enforces unit testing, ruff linting, and SAST scanner before deployment."
    },
    "CC9.1": {
      "title": "Vendor Risk & Dependency Version Pinning",
      "status": "PASS",
      "evidence": "Dependency versions pinned in pyproject.toml."
    }
  }
}
```

---

## Automated Verification Test Suite Matrix (Pre-Audit Proofs)

The entire SOC 2 Type II control matrix and automated evidence collection engine are continuously validated through unit test suites under [backend/tests/unit/](../../backend/tests/unit):

| Test Suite | Targeted Control | Test Function | Verified Behavior | Status |
| :--- | :--- | :--- | :--- | :---: |
| [test_security_controls_audit.py](../../backend/tests/unit/test_security_controls_audit.py) | **CC6.1** | `test_cc6_1_all_endpoints_authenticated` | Validates all non-public endpoints require authentication in OpenAPI schema | ✅ Pass |
| [test_security_controls_audit.py](../../backend/tests/unit/test_security_controls_audit.py) | **CC6.3** | `test_cc6_3_no_secrets_in_env` | Confirms zero plaintext passwords/secrets exposed in environment variables | ✅ Pass |
| [test_security_controls_audit.py](../../backend/tests/unit/test_security_controls_audit.py) | **CC7.1** | `test_cc7_1_audit_log_has_entries` | Asserts cryptographic audit log engine produces active tamper-evident records | ✅ Pass |
| [test_security_controls_audit.py](../../backend/tests/unit/test_security_controls_audit.py) | **CC6.1–CC9.1** | `test_generate_soc2_evidence_report` | Confirms `SecurityComplianceEngine` generates 100% compliant report | ✅ Pass |
| [test_security_controls_audit.py](../../backend/tests/unit/test_security_controls_audit.py) | **API** | `test_soc2_evidence_endpoint` | Validates GET and POST `/v1/compliance/soc2-evidence` endpoints return `200 OK` | ✅ Pass |
| [test_security_controls_audit.py](../../backend/tests/unit/test_security_controls_audit.py) | **CC6.6** | `test_sql_injection_rejected` | Asserts WAF intercepts and blocks malicious SQL injection payloads | ✅ Pass |
| [test_security_controls_audit.py](../../backend/tests/unit/test_security_controls_audit.py) | **CC6.6** | `test_env_file_not_exposed` | Confirms sensitive configuration and git paths are blocked with `403` | ✅ Pass |
| [test_security_controls_audit.py](../../backend/tests/unit/test_security_controls_audit.py) | **CC6.6** | `test_auth_lockout_after_5_failures` | Verifies 5 consecutive authentication failures trigger client IP lockout | ✅ Pass |
| [test_security_controls_audit.py](../../backend/tests/unit/test_security_controls_audit.py) | **CC6.6** | `test_cors_whitelist_enforced_no_wildcards` | Ensures wildcard CORS is rejected and explicit origins receive headers | ✅ Pass |
| [test_security_controls_audit.py](../../backend/tests/unit/test_security_controls_audit.py) | **CC6.6** | `test_cors_rejects_unauthorized_origins` | Verifies unauthorized external origins are stripped of CORS headers | ✅ Pass |
| [test_security_compliance.py](../../backend/tests/unit/test_security_compliance.py) | **Framework** | `test_security_controls_auditing` | Asserts compliance engine audits SOC2, ISO27001, and GDPR controls | ✅ Pass |
| [test_security_compliance.py](../../backend/tests/unit/test_security_compliance.py) | **Framework** | `test_compliance_attestation_report_generation` | Confirms overall compliance attestation score equals 100.0% | ✅ Pass |
| **Total Verified** | **2 Test Suites** | **SOC 2 Type II Security Controls** | **12 Unit Tests** | **100% Pass** |
