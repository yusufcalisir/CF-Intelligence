# SOC 2 Type II Controls Matrix & Automated Evidence Verification

This document defines the SOC 2 Type II Trust Services Criteria (TSC) security controls implemented in the CFI Platform and details the automated verification endpoints and unit test proofs for continuous compliance auditing.

---

## SOC 2 Trust Services Criteria (TSC) Matrix

| Control ID | Control Title | Implementation Description | Automated Verification Method | Control Status |
| :--- | :--- | :--- | :--- | :---: |
| **CC6.1** | Logical Access Controls & Route Authentication | All non-public API endpoints require JWT authentication tokens or X.509 mTLS certificates. Perimeter WAF filters malicious traffic. | `POST /v1/compliance/soc2-evidence`<br>`test_cc6_1_all_endpoints_authenticated()` | `PASS` |
| **CC6.2** | Transmission Encryption (TLS/SSL) | All inter-node gRPC communications and database connections mandate TLS (`sslmode=require`). | `POST /v1/compliance/soc2-evidence`<br>`DATABASE_URL` TLS enforcement test | `PASS` |
| **CC6.3** | Secrets Management & Vault/KMS Protection | API keys, database credentials, and RSA keys are stored in HashiCorp Vault or AWS KMS; no plaintext secrets in environment variables. | `POST /v1/compliance/soc2-evidence`<br>`test_cc6_3_no_secrets_in_env()` | `PASS` |
| **CC6.6** | Perimeter Security & WAF Protection | WAF rules inspect incoming HTTP/gRPC headers and block SQL injection, XSS, and unauthorized cross-namespace requests. | WAF Web ACL rules (`AWSManagedRulesCommonRuleSet`, `AWSManagedRulesSQLiRuleSet`) | `PASS` |
| **CC7.1** | Immutable Security Audit Logging | Cryptographic SHA-256 hash chain logs every data access, case state modification, and governance vote. | `POST /v1/compliance/soc2-evidence`<br>`test_cc7_1_audit_log_has_entries()` | `PASS` |
| **CC8.1** | Automated Change Management | All codebase changes require GitHub Actions CI pass (unit tests, ruff linting, SAST security audit) before merging to `main`. | GitHub Actions CI workflow & automated test suite | `PASS` |
| **CC9.1** | Vendor Risk & Dependency Pinning | All third-party Python and Node.js package dependencies are pinned to specific version hashes in `pyproject.toml` and `package-lock.json`. | `POST /v1/compliance/soc2-evidence`<br>`pyproject.toml` dependency audit | `PASS` |

---

## Automated SOC 2 Evidence API

Auditors and security teams can query real-time evidence reports programmatically:

```http
POST /v1/compliance/soc2-evidence HTTP/1.1
Host: api.cfi-platform.org
Authorization: Bearer <AUDITOR_JWT_TOKEN>
```

### Sample Response (`200 OK`)

```json
{
  "report_id": "soc2_evidence_20260729_134500",
  "timestamp": "2026-07-29T10:45:00Z",
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
      "evidence": "Database URL specifies encrypted TLS transport."
    },
    "CC6.3": {
      "title": "Secrets Management & Vault/KMS Envelope Encryption",
      "status": "PASS",
      "evidence": "All credentials managed via Vault PKI or AWS KMS envelope encryption."
    },
    "CC7.1": {
      "title": "Immutable Cryptographic Audit Logging",
      "status": "PASS",
      "evidence": "SHA-256 tamper-evident hash chain active; all data access events logged."
    },
    "CC8.1": {
      "title": "CI/CD Automated Change Management & Security Scanning",
      "status": "PASS",
      "evidence": "GitHub Actions CI pipeline enforces unit testing, ruff linting, and SAST scanner."
    },
    "CC9.1": {
      "title": "Vendor Risk & Dependency Version Pinning",
      "status": "PASS",
      "evidence": "Dependency versions pinned in pyproject.toml."
    }
  }
}
```
