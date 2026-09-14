# 🛡️ Enterprise Security, Vulnerability Disclosure & Bug Bounty Policy

The Collaborative Fraud Intelligence (CFI) platform secures cross-institution federated learning, confidential financial transactions, and inter-bank model parameters. We prioritize software security, differential privacy, zero-PII data sovereignty, and defense-in-depth isolation across all consortium banking nodes.

This policy defines supported versions, core cryptographic and architectural invariants, responsible vulnerability disclosure protocols, response SLAs, Safe Harbor protections, and Bug Bounty recognition.

---

## 📌 1. Supported Versions

We provide active security patches, vulnerability advisories, and dependency maintenance for the following release branches:

| Version Branch | Supported Status | Security Patch Policy | Automated Tooling Verification |
| :--- | :---: | :--- | :--- |
| **`v2.x` (Main / Production)** | ✅ **ACTIVE SUPPORT** | Full security updates, continuous CVE remediation, zero-day hotfixes, and automated dependency patching. | Bandit SAST, pip-audit, Dependabot, CodeQL |
| **`v1.x` (Legacy Maintenance)** | ⚠️ **SECURITY ONLY** | Critical security patches (`SEV1_CRITICAL` only). End-of-Life: December 2026. | Monthly dependency audit scans |
| **`< v1.0.0`** | ❌ **UNSUPPORTED** | No security patches provided. Immediate upgrade to `v2.x` required. | Deprecated |

---

## 🔐 2. Core Architectural Security Invariants & Cryptographic Perimeters

The platform enforces non-negotiable security and privacy guarantees across all deployments:

1. **Zero Raw PII Transmission**:
   - No unmasked PANs, IBANs, national IDs, or customer names leave local bank premises.
   - Identifiers must be type-salted HMAC-SHA256 hashed prior to inter-node communication or graph edge formation.
2. **Differential Privacy Budget Bounding**:
   - Federated gradient updates must satisfy $(\epsilon, \delta)$-Differential Privacy under Rényi DP accounting ($\epsilon \le 1.0, \delta = 10^{-5}$) with $L_2$ gradient clipping bound $C = 1.0$ and calibrated Gaussian noise multiplier ($\sigma \ge 1.1$).
   - Enforced by [`label_privacy_guard.py`](file:///c:/Users/Yusuf/Desktop/projects/Privacy-preserving%20cross-bank%20fraud%20detection%20using%20Federated%20Learning/backend/app/domain/label_privacy_guard.py) and [`privacy_defense.py`](file:///c:/Users/Yusuf/Desktop/projects/Privacy-preserving%20cross-bank%20fraud%20detection%20using%20Federated%20Learning/backend/app/presentation/routers/privacy_defense.py).
3. **Hardware-Isolated Homomorphic Aggregation**:
   - Inter-bank weight aggregation executes inside Intel SGX Enclave v2 or AWS Nitro Enclaves with IAS/DCAP remote attestation.
   - TenSEAL CKKS homomorphic encryption ensures intermediate gradient parameters remain confidential even from the central coordinator.
4. **Hard Multi-Tenant Isolation & Zero Data Bleed**:
   - Bank tenant environments are partitioned via isolated database schemas (`tenant_{bank_id}`), dynamic ABAC policies, and PostgreSQL Row-Level Security (RLS).
   - Broken Object Level Authorization (BOLA / IDOR) and cross-tenant parameter tampering are treated as `SEV1_CRITICAL`.
5. **KMS Versioned Envelope Cryptography**:
   - Confidential data keys utilize AES-256-GCM (`v2` versioned tokens) managed via HashiCorp Vault PKI / KV v2 or AWS KMS with fail-closed key revocation.
   - Zero plaintext secrets or private keys exist in environment variables, codebases, or unencrypted storage volumes.
6. **Byzantine Poisoning & Adversarial Robustness**:
   - Central model aggregation implements robust aggregation algorithms: Coordinate-wise Median, Trimmed Mean, Krum, Multi-Krum, Bulyan, and FoolsGold cosine-similarity defenses to neutralize malicious sybil gradient attacks.
7. **Dynamic Model Watermarking & Intellectual Property Protection**:
   - Production federated models embed dynamic cryptographic watermarks and trigger backdoors.
   - Backdoor signatures and Kolmogorov-Smirnov statistical tests continuously detect model extraction and IP theft attempts.
8. **Perimeter WAF & Defense-in-Depth Gateway**:
   - Edge traffic passes through [`PerimeterWAFGuard`](file:///c:/Users/Yusuf/Desktop/projects/Privacy-preserving%20cross-bank%20fraud%20detection%20using%20Federated%20Learning/backend/app/infrastructure/security/perimeter_waf.py) enforcing SQL injection rejection, XSS blocking, sensitive path filtering (`/.env`, `/admin`, `/.git`), brute-force IP lockout (5 failures / 300s window), and strict CORS domain whitelisting.

---

## 🚨 3. Reporting a Vulnerability

If you discover a security vulnerability, privacy leakage, cryptographic flaw, or access control bypass, please report it responsibly using one of the channels below:

### Option A: GitHub Private Vulnerability Reporting (Preferred)
Submit your report directly through GitHub's encrypted advisory workflow:
1. Navigate to the repository's **Security** tab: [GitHub Security Advisories](https://github.com/yusufcalisir/CF-Intelligence/security/advisories).
2. Click **"Report a vulnerability"**.
3. Provide full reproduction details, attack vectors, and proof-of-concept scripts.

### Option B: Encrypted Email (Security Response Team)
If you prefer email or do not have a GitHub account:
- 📧 **Primary Security Email**: `security@collaborative-fraud-intel.org`
- 🚨 **Emergency On-Call Escalation**: `secops@cfi-platform.org` (Subject: `[SECURITY-DISCLOSURE] <Short Vulnerability Summary>`)

### PGP Public Key Details
For sensitive disclosure disclosures via email, please encrypt your communication with our operational PGP key:
- **Key ID**: `0x4F9B8C7A2E109D3F`
- **Fingerprint**: `4F9B 8C7A 2E10 9D3F 851B  C602 1A3E 7B90 8F24 5D1E`
- **Key Server**: `hkps://keys.openpgp.org`

### What to Include in Your Report
To expedite assessment and triage, please include:
- Affected API endpoint, source file, component, or network protocol.
- Step-by-step reproduction instructions or a minimal Proof-of-Concept (PoC) script / cURL command.
- Attack prerequisites (e.g., required role: `analyst`, `bank_admin`, or unauthenticated).
- Potential impact assessment across participating consortium banking nodes.

---

## ⏱️ 4. Response Timelines & Severity Classification Matrix

Our triage SLAs and resolution targets strictly mirror the [Enterprise Incident Response Playbook](file:///c:/Users/Yusuf/Desktop/projects/Privacy-preserving%20cross-bank%20fraud%20detection%20using%20Federated%20Learning/docs/incident_response_playbook.md) and SOC 2 Type II operational commitments:

| Severity Tier | CVSS v3.1 Range | Example Vulnerability Scenarios | Initial Acknowledgment | On-Call Triage SLA | Target Remediation Window |
| :--- | :---: | :--- | :---: | :---: | :---: |
| **`SEV1_CRITICAL`** | `9.0 – 10.0` | Raw PII plaintext leakage, unauthenticated Remote Code Execution (RCE), private key / HSM extraction, cross-tenant database bleed, zero-day cryptographic compromise. | **`< 24 Hours`** | **$\le 15\text{ Minutes}$** | **$\le 7\text{ Business Days}$** *(Hotfix MTTR $\le 1\text{ Hour}$)* |
| **`SEV2_MAJOR`** | `7.0 – 8.9` | Differential privacy budget bypass, Byzantine aggregation filter bypass, authentication bypass, BOLA / IDOR case manipulation. | **`< 48 Hours`** | **$\le 1\text{ Hour}$** | **$\le 14\text{ Business Days}$** |
| **`SEV3_MODERATE`** | `4.0 – 6.9` | Rate-limiting bypass, localized CSRF, minor information disclosure without PII exposure, non-exploitable timing anomalies. | **`< 72 Hours`** | **$\le 4\text{ Hours}$** | **$\le 30\text{ Business Days}$** |
| **`SEV4_LOW`** | `0.1 – 3.9` | Cosmetic UI issues, non-sensitive error messages with unique incident IDs, best-practice header recommendations. | **`< 5 Days`** | Next Business Day | Next Scheduled Minor Release |

---

## 🔄 5. Coordinated Vulnerability Disclosure (CVD) Process

The CFI platform follows a standard 90-day Coordinated Vulnerability Disclosure (CVD) lifecycle:

```
[ Researcher Submits PoC ] ──► [ Triage & Severity Classification (<24-48h) ]
                                            │
                                            ▼
[ Patch Verification & Testing ] ◄── [ Engineering Hotfix Development ]
            │
            ▼
[ CVE Assignment via GitHub CNA ] ──► [ 90-Day Embargo & Coordinated Public Advisory ]
```

1. **Initial Triage & Confirmation**: Within the SLA window, the security team validates the reproduction steps and assigns an internal tracking ID.
2. **Remediation & Testing**: Engineering develops and validates patches in a staging environment with full regression test coverage.
3. **CVE Assignment**: For qualifying vulnerabilities, we request a Common Vulnerabilities and Exposures (CVE) identifier via GitHub Security Advisories (authorized CNA).
4. **Coordinated Release**: The patch and security advisory are released concurrently across production repositories and Docker container registries.

---

## 🤝 6. Safe Harbor Policy

We strongly support and endorse responsible security research. We commit not to pursue legal action against security researchers who adhere to the following principles:

- **Good-Faith Effort**: Conduct research in good faith to avoid privacy violations, data destruction, degradation of service, or financial impact.
- **Account & Data Boundaries**: Only interact with your own test accounts, mock consortium nodes, or local developer instances. Never access, modify, or download real banking customer data.
- **No Volumetric Denial of Service**: Refrain from high-volume network flooding (DDoS/DoS) or resource exhaustion targeting production gateways.
- **Responsible Embargo**: Allow our engineering team a reasonable resolution window (per the CVD timeline) before publishing findings or disclosing technical exploit details publicly.

If your research complies with these guidelines, we consider it **authorized**, will cooperate with you to understand and resolve the issue quickly, and will not initiate legal proceedings.

---

## 🏆 7. Bug Bounty & Security Recognition Program

To demonstrate our appreciation for responsible researchers who help protect the banking consortium:

- **Hall of Fame**: Researchers who report confirmed `SEV1` through `SEV3` vulnerabilities will be credited in our public Security Hall of Fame, release notes, and GitHub Security Advisories (unless anonymity is requested).
- **Commemorative Rewards & Swag**: Verified findings may qualify for commemorative platform tokens, digital security badges, or bug bounty honorariums according to vulnerability impact.

---

## 📚 8. Related Security & Compliance Documentation

For technical specifications, testing boundaries, and automated audit proofs, refer to:

- [Penetration Testing Scope & Rules of Engagement](file:///c:/Users/Yusuf/Desktop/projects/Privacy-preserving%20cross-bank%20fraud%20detection%20using%20Federated%20Learning/docs/security/pentest_scope.md) — Authorized endpoints, in-scope interfaces, and testing constraints.
- [SOC 2 Type II Controls Matrix](file:///c:/Users/Yusuf/Desktop/projects/Privacy-preserving%20cross-bank%20fraud%20detection%20using%20Federated%20Learning/docs/security/soc2_type2_controls_matrix.md) — Trust Services Criteria implementations and automated verification proofs.
- [Enterprise Incident Response Playbook](file:///c:/Users/Yusuf/Desktop/projects/Privacy-preserving%20cross-bank%20fraud%20detection%20using%20Federated%20Learning/docs/incident_response_playbook.md) — 24/7 on-call escalation, P0–P4 runbooks, and recovery protocols.
- [Master Threat Model & Attack Surface](file:///c:/Users/Yusuf/Desktop/projects/Privacy-preserving%20cross-bank%20fraud%20detection%20using%20Federated%20Learning/docs/threat_model.md) — Formal STRIDE/DREAD threat evaluations.
- [Perimeter WAF Guard Implementation](file:///c:/Users/Yusuf/Desktop/projects/Privacy-preserving%20cross-bank%20fraud%20detection%20using%20Federated%20Learning/backend/app/infrastructure/security/perimeter_waf.py) — WAF rule definitions and payload filters.
- [Immutable Cryptographic Audit Chain](file:///c:/Users/Yusuf/Desktop/projects/Privacy-preserving%20cross-bank%20fraud%20detection%20using%20Federated%20Learning/backend/app/infrastructure/security/immutable_audit_chain.py) — Append-only SHA-256 block hash chaining.
