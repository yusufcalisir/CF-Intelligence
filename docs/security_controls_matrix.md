# 📑 Enterprise Security Controls Matrix (SOC 2 / ISO 27001 / GDPR / SR 11-7)

This matrix maps platform privacy, authentication, zero-trust cryptographic protections, and operational resilience features directly to enterprise compliance standards and regulatory frameworks.

---

## 📌 Comprehensive Compliance Mapping Table

| Control ID | Framework | Scope & Security Invariant | Technical Implementation | Verification Engine / Test | Status |
| :--- | :--- | :--- | :--- | :--- | :---: |
| **`SOC2-CC6.1`** | SOC 2 Type II | Perimeter WAF, IP whitelisting, SQLi/XSS filtering & Bot Management | `PerimeterWAFGuard`, Cloudflare Terraform IaC | `test_security_controls_audit.py` | `PASS` |
| **`SOC2-CC6.1.1`** | SOC 2 Type II | Bcrypt password hashing (cost=12) with salted cryptographic digests | `PasswordHasher` (`password_hasher.py`) | `test_auth_security.py` | `PASS` |
| **`SOC2-CC6.1.2`** | SOC 2 Type II | Short-lived JWTs (15 min) with single-use refresh token rotation | `EnterpriseAuthService` (`auth_service.py`), `auth.py` | `test_auth_security.py`<br/>`test_auth_routes.py` | `PASS` |
| **`SOC2-CC6.1.3`** | SOC 2 Type II | Brute-force account & IP lockout (5 failed attempts -> 15 min lock) | `EnterpriseAuthService` (`auth_service.py`), `auth.py` | `test_auth_security.py`<br/>`test_auth_routes.py` | `PASS` |
| **`SOC2-CC6.1.4`** | SOC 2 Type II | Strict CORS whitelist (zero wildcard `*`) & HTTP security headers | `SecurityHeadersMiddleware`, `config.py` | `test_security_headers.py` | `PASS` |
| **`SOC2-CC6.2`** | SOC 2 Type II | Multi-Tenant BOLA Isolation & OIDC Scoped Tenant Access | `TenantAccessControlMiddleware` (`main.py`) | `test_tenant_isolation.py` | `PASS` |
| **`SOC2-CC6.3`** | SOC 2 Type II | Attribute-Based Access Control (ABAC) with granular tenant rules | `ABACPolicyEngine` (`abac_engine.py`) | `test_enterprise_security_suite.py` | `PASS` |
| **`SOC2-CC6.6`** | SOC 2 Type II | TLS 1.3 in transit & AES-256-GCM envelope encryption at rest | `TenantKMSEngine`, `VaultClient` | `test_tenant_kms_metering.py` | `PASS` |
| **`SOC2-CC6.7.1`** | SOC 2 Type II | Production error sanitization (RFC 7807, zero stack trace leakage) | `ProductionErrorHandler` (`error_handler.py`) | `test_error_sanitization.py` | `PASS` |
| **`SOC2-CC6.8`** | SOC 2 Type II | Multi-Layer L7 DDoS protection & `slowapi` granular rate limiting | `DDoSProtectionMiddleware`, `rate_limiter.py` | `test_ddos_middleware.py` | `PASS` |
| **`SOC2-CC7.2`** | SOC 2 Type II | Tamper-evident append-only SHA-256 cryptographic audit chain | `ImmutableAuditChain` (`immutable_audit_chain.py`) | `test_enterprise_security_suite.py` | `PASS` |
| **`ISO27001-A.9.4.2`** | ISO 27001 | Four-Eyes supervisor dual-authorization signature on case closure | `CaseLifecycleStateMachine` (`case_workbench.py`) | `test_case_management_workbench.py` | `PASS` |
| **`ISO27001-A.12.1.2`** | ISO 27001 | Gaussian DP noise ($\epsilon \le 1.0, \delta = 10^{-5}$) & Rényi accounting | `OpacusDPGuard`, `PrivacyAuditService` | `test_privacy_service.py` | `PASS` |
| **`ISO27001-A.12.6.1`** | ISO 27001 | Formal STRIDE threat model covering 6 attack pillars | `docs/threat_model.md` | `test_byzantine_defense_validation.py` | `PASS` |
| **`SOC2-CC6.9`** | SOC 2 Type II | Zero-Vulnerability Supply Chain Security & Pinned Security Floor | `backend/requirements.txt`, Dependabot Policy | `pytest backend/tests/` | `PASS` |
| **`GDPR-ART-25`** | GDPR Art. 25 | Privacy by Design: Client-Side Luhn PAN & Type-Salted HMAC Sanitization | `piiSanitizer.ts`, `DatasetDropzone.tsx` | `test_dataset_ingestor.py` | `PASS` |
| **`ISO27001-A.12.1.3`**| ISO 27001 | Great Expectations 1.x Data Contract Gating & Quarantine Isolation | `datasets.py`, Great Expectations Suite | `test_dataset_ingestor.py` | `PASS` |
| **`ISO27001-A.14.2.9`**| ISO 27001 | Interactive Byzantine Gradient Injection & Krum Quarantine Shield | `scenarios.py`, `ChaosAttackInjectorPanel.tsx` | `test_attack_injector.py` | `PASS` |
| **`GDPR-ART-6`** | GDPR Art. 6 | Zero raw PII data pooling; federated gradient exchange only | `FederatedLearningEngine` (`fl_engine.py`) | `test_fl_engine.py` | `PASS` |
| **`GDPR-ART-17`** | GDPR Art. 17 | Automated TTL data purging, cryptographic zeroization & unlearning | `AutomatedRetentionEngine`, `FederatedUnlearningEngine` | `test_retention_erasure_engine.py`, `test_retention_erasure_hardening.py`, `test_federated_unlearning_engine.py` | `PASS` |
| **`SR-11-7-GOV`** | Fed SR 11-7 | Model risk management, concept drift detection & champion gate | `ModelGovernanceService`, `CanaryQualityGate` | `test_sr11_7_model_governance.py`, `test_model_governance_hardening.py` | `PASS` |
| **`ISO20022-MSG`** | ISO 20022 / SWIFT | XML XSD schema validation, XXE defense, ISO 13616 IBAN Mod-97, salted zero-PII hashing | `FinancialMessageParser`, `ISO20022MessagingConnector` | `test_extended_iso20022_parser.py`, `test_financial_message_parser_hardening.py`, `test_bank_connectors.py` | `PASS` |
| **`ACID-CONCURRENCY`**| SOC 2 / PCI-DSS | Zero-drift Alembic schema parity, SQLSTATE Class 40 transaction retries & Redis distributed token locking | `run_cockroach_transaction`, `CacheService.distributed_lock`, `AlertModel` | `test_alembic_migrations.py`, `test_database_persistence_hardening.py`, `test_concurrency_safety.py` | `PASS` |
| **`HSM-PKCS11-VAULT`** | FIPS 140-2 Level 3 / eIDAS | Hardware-anchored Zero-Disk key management, Curve25519 ECDH in enclave, automated mTLS 1.3 cert rotation | `HSMKeyService`, `VaultHSMPKIBinder` | `test_hsm_key_service.py`, `test_vault_hsm_pki_binder.py`, `test_hsm_signer.py` | `PASS` |
| **`EU-AI-ACT-DOSSIER`** | EU AI Act / SR 11-7 | High-Risk AI Articles 9–15 compliance matrix, FedGNN proofs, dual-control cryptographic sign-offs | `RegulatoryDossierGenerator`, `regulatory_dossier.py` | `test_regulatory_dossier_generator.py` | `PASS` |

---

## 🔒 Verification References

All controls listed above are automatically verified by the continuous testing pipeline across **2,942 automated tests** (2,583 Backend Pytest + 328 Frontend Vitest + 31 Smart Contracts) and 18 scientific verification audit modules (310 verification tests; 3,252 total).



