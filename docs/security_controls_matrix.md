# 📑 Enterprise Security Controls Matrix (SOC2 / ISO 27001 / GDPR)

This matrix maps platform privacy and security features directly to enterprise compliance standards.

---

## 📌 Compliance Mapping Table

| Control ID | Framework | Feature / Implementation | Verification Engine | Status |
| :--- | :--- | :--- | :--- | :--- |
| **`SOC2-CC6.1`** | SOC2 Type II | Perimeter WAF, IP whitelisting, SQLi/XSS filtering. | `PerimeterWAFGuard` | `PASS` |
| **`SOC2-CC6.2`** | SOC2 Type II | Multi-Tenant BOLA Isolation & OIDC Scoped Tenant Access. | `TenantAccessControlMiddleware` | `PASS` |
| **`SOC2-CC6.6`** | SOC2 Type II | TLS 1.3 in transit & AES-256 encryption at rest. | `SecurityComplianceEngine` | `PASS` |
| **`SOC2-CC6.8`** | SOC2 Type II | Multi-Layer L7 DDoS & slowapi Granular Rate Limiting. | `DDoSProtectionMiddleware` | `PASS` |
| **`ISO27001-A.12.1.2`** | ISO 27001 | Gaussian DP noise ($\epsilon \le 2.0$) & zero-PII leak guard. | `LabelPrivacyGuard` | `PASS` |
| **`ISO27001-A.9.4.2`** | ISO 27001 | Four-Eyes supervisor dual-authorization signature. | `CaseLifecycleStateMachine` | `PASS` |
| **`GDPR-ART-17`** | GDPR Art. 17 | Automated TTL data purging & Right-to-be-Forgotten. | `AutomatedRetentionEngine` | `PASS` |

