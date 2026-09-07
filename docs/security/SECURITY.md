# 🛡️ Enterprise Security & Responsible Vulnerability Disclosure Policy

The Collaborative Fraud Intelligence (CFI) platform secures cross-institution federated learning, confidential financial transactions, and inter-bank model parameters. We prioritize software security, differential privacy, zero-PII data sovereignty, and defense-in-depth isolation.

---

## 📌 Supported Versions

We provide active security patches, vulnerability advisories, and dependency maintenance for the following releases:

| Version Branch | Supported Status | Security Patch Policy |
| :--- | :---: | :--- |
| **`v2.x` (Main / Production)** | ✅ **ACTIVE SUPPORT** | Regular security updates, dependency vulnerability remediation, and CVE patches. |
| **`v1.x` (Legacy Maintenance)** | ⚠️ **SECURITY ONLY** | Critical security patches (`SEV1_CRITICAL` only). End-of-Life: December 2026. |
| **`< v1.0.0`** | ❌ **UNSUPPORTED** | No security patches provided. Immediate upgrade to `v2.x` required. |

---

## 🔐 Core Security Invariants & In-Scope Assets

The platform maintains non-negotiable security and privacy guarantees:
1. **Zero Raw PII Transmission**: No unmasked PANs, IBANs, or customer names leave local bank premises. Identifiers must be type-salted HMAC-SHA256 hashed.
2. **Differential Privacy Budget Bounding**: Gradient updates must satisfy $(\epsilon, \delta)$-DP under Rényi DP accounting ($\epsilon \le 1.0, \delta = 10^{-5}$).
3. **Hardware-Isolated Homomorphic Aggregation**: Intel SGX Enclave v2 / AWS Nitro Enclaves must verify IAS remote attestation before homomorphic summation.
4. **Hard Multi-Tenant Isolation**: Cross-tenant data bleed, BOLA/IDOR query parameter tampering, or SQL injection via tenant identifiers are critical vulnerabilities.
5. **KMS Versioned Envelope Cryptography**: Data keys must use AES-256-GCM (`v2` versioned tokens) with fail-closed revocation.

---

## 🚨 Reporting a Vulnerability

If you discover a security vulnerability, privacy leakage, or cryptographic flaw:

1. **Do NOT file a public GitHub issue or discuss it in public chat channels.**
2. Send an encrypted vulnerability disclosure report to our Security Incident Response Team:  
   📧 **`security@collaborative-fraud-intel.org`**
3. **PGP Public Key**: Encrypt your disclosure using our operational PGP key:
   - **Key ID**: `0x4F9B8C7A2E109D3F`
   - **Fingerprint**: `4F9B 8C7A 2E10 9D3F 851B  C602 1A3E 7B90 8F24 5D1E`
4. **Report Contents**:
   - Detailed description of the vulnerability and attack vector.
   - Proof-of-Concept (PoC) scripts, cURL commands, or reproduction steps.
   - Potential impact assessment across participating banking nodes.

---

## ⏱️ Response Timelines & Severity Classification

| Severity Tier | Definition & Examples | Response SLA | Target Remediation Window |
| :--- | :--- | :---: | :---: |
| **`SEV1_CRITICAL`** | Raw PII plaintext leakage, remote code execution (RCE), private key / HSM extraction, cross-tenant database bleed. | **`< 24 Hours`** | **$\le 7\text{ Business Days}$** |
| **`SEV2_MAJOR`** | Differential privacy budget bypass, Byzantine aggregation filter bypass, authentication bypass. | **`< 48 Hours`** | **$\le 14\text{ Business Days}$** |
| **`SEV3_MODERATE`** | Rate-limiting bypass, localized CSRF, minor information leakage without PII exposure. | **`< 72 Hours`** | **$\le 30\text{ Business Days}$** |
| **`SEV4_LOW`** | Cosmetic UI issues, non-sensitive error messages with unique incident IDs. | **`< 5 Days`** | Next Scheduled Minor Release |

---

## 🤝 Safe Harbor Policy

We endorse responsible security research. We will not pursue legal action against security researchers who:
- Make a good-faith effort to avoid privacy violations, data destruction, and service disruption.
- Only interact with their own test accounts or simulated demo nodes, without accessing third-party banking data.
- Allow our engineering team a reasonable resolution window before public disclosure.
