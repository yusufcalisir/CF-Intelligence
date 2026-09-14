# 🏦 Bank Node Automated Onboarding & Operations Guide

This operational guide details the end-to-end architecture, cryptographic credentialing, and configuration workflows for onboarding a new financial institution node to the **Collaborative Fraud Intelligence (CF-Intelligence)** platform.

The onboarding subsystem ([`BankOnboardingService`](file:///c:/Users/Yusuf/Desktop/projects/Privacy-preserving%20cross-bank%20fraud%20detection%20using%20Federated%20Learning/backend/app/application/services/bank_onboarding_service.py) and [`onboarding.py`](file:///c:/Users/Yusuf/Desktop/projects/Privacy-preserving%20cross-bank%20fraud%20detection%20using%20Federated%20Learning/backend/app/presentation/routers/onboarding.py)) automates institution registration, mutual TLS (mTLS) X.509 certificate issuance, PostgreSQL engine-level tenant schema isolation, HashiCorp Vault transit KMS key provisioning, and connector YAML generation.

---

## 🏛️ Automated Onboarding Architecture

```mermaid
sequenceDiagram
    autonumber
    participant BankIT as Bank Node IT / Security
    participant API as Onboarding API Router (/api/v1/onboarding)
    participant Service as BankOnboardingService
    participant DB as PostgreSQL (TenantConfigModel)
    participant PKI as X.509 Cert Engine
    participant Servicer as gRPC Servicer (Anti-TOFU)
    participant Vault as Vault Transit KMS
    participant Daemon as cfi-daemon (Bank Node)

    BankIT->>API: POST /api/v1/onboarding/register (BankRegisterRequest)
    API->>Service: register_bank(bank_id, legal_name, jurisdiction, ...)
    Service->>DB: INSERT TenantConfigModel (status=PENDING_VERIFICATION)
    Service->>PKI: issue_mtls_certificate(bank_id)
    PKI-->>Service: cert_pem (RSA 2048, CN={bank_id}.client...), key_pem, SHA-256 fingerprint
    Service->>DB: UPDATE cert_fingerprint, cert_expires_at
    Service->>Servicer: register_bank_fingerprint(bank_id, fingerprint)
    Note over Servicer: Authoritative fingerprint binding (Anti-TOFU protection)
    Service->>DB: provision_tenant_schema(bank_id) -> CREATE SCHEMA tenant_{bank_id}
    Service->>Vault: provision_kms_key(bank_id) -> transit/keys/tenant_{bank_id}
    Service->>Service: generate_connector_config(bank_id) -> YAML
    Service->>DB: activate_bank(bank_id) -> status=ACTIVE, activated_at=NOW()
    Service-->>API: BankOnboardingBundleResponse
    API-->>BankIT: HTTP 201 Created (Certs, Keys, YAML Config, Fingerprint)

    Note over BankIT,Daemon: Deployment Phase
    BankIT->>Daemon: Install Certs & Start Daemon (cfi-daemon --config bank_alpha.yaml)
    Daemon->>Servicer: gRPC RegisterClient(bank_id, fingerprint)
    Servicer->>Servicer: Verify against authoritative registry (reject TOFU/spoofing)
    Servicer-->>Daemon: session_token (grpc_sess_...), assigned_cluster_id
```

### Core Implementation Files

- **Application Service**: [`backend/app/application/services/bank_onboarding_service.py`](file:///c:/Users/Yusuf/Desktop/projects/Privacy-preserving%20cross-bank%20fraud%20detection%20using%20Federated%20Learning/backend/app/application/services/bank_onboarding_service.py)
- **REST Presentation Router**: [`backend/app/presentation/routers/onboarding.py`](file:///c:/Users/Yusuf/Desktop/projects/Privacy-preserving%20cross-bank%20fraud%20detection%20using%20Federated%20Learning/backend/app/presentation/routers/onboarding.py)
- **gRPC Coordination Servicer**: [`backend/app/infrastructure/grpc/servicer.py`](file:///c:/Users/Yusuf/Desktop/projects/Privacy-preserving%20cross-bank%20fraud%20detection%20using%20Federated%20Learning/backend/app/infrastructure/grpc/servicer.py)
- **Domain Entities & Enums**: [`backend/app/domain/entities.py`](file:///c:/Users/Yusuf/Desktop/projects/Privacy-preserving%20cross-bank%20fraud%20detection%20using%20Federated%20Learning/backend/app/domain/entities.py#L128), [`backend/app/domain/enums.py`](file:///c:/Users/Yusuf/Desktop/projects/Privacy-preserving%20cross-bank%20fraud%20detection%20using%20Federated%20Learning/backend/app/domain/enums.py#L215)
- **Web UI Onboarding Studio**: [`frontend/src/pages/BankOnboardingPage.tsx`](file:///c:/Users/Yusuf/Desktop/projects/Privacy-preserving%20cross-bank%20fraud%20detection%20using%20Federated%20Learning/frontend/src/pages/BankOnboardingPage.tsx)
- **Automated Unit Tests**: [`backend/tests/unit/test_bank_onboarding.py`](file:///c:/Users/Yusuf/Desktop/projects/Privacy-preserving%20cross-bank%20fraud%20detection%20using%20Federated%20Learning/backend/tests/unit/test_bank_onboarding.py)

---

## 1. Prerequisites & Transport Requirements

Before initiating node registration, the institution's IT/Security team must verify:
- **System Requirements:** Python 3.12+, Docker/Kubernetes container runtime, and at least 4 GB RAM / 2 vCPUs for local training.
- **Admin Access:** API key or administrative credentials to issue onboarding calls to `/api/v1/onboarding/register`.
- **Outbound Network Access:** Outbound TCP port `50051` (gRPC mTLS) open to `coordinator.cf-intelligence.io`.

### Network & Transport Layer Matrix

> [!IMPORTANT]
> All CF-Intelligence bank→coordinator communication is **mandatory mTLS over gRPC**.
> Plain HTTP connections and TLS 1.2 are **refused at the transport layer** — there is no insecure fallback.

| Requirement | Detail |
|---|---|
| **Protocol** | gRPC over TLS 1.3 only (TLS 1.2 rejected) |
| **Port** | TCP `50051` outbound from bank network to coordinator |
| **Authentication** | Mutual TLS — both client and server present X.509 certificates |
| **Client Cert CN** | Must match `{bank_id}.client.cf-intelligence.io` (issued by onboarding API) |
| **HTTP Fallback** | None — insecure channels are rejected at the server interceptor level |
| **Coordinator FQDN** | `coordinator.cf-intelligence.io` — add to institutional firewall allowlist |
| **Cert Rotation** | gRPC client auto-detects cert file changes and recycles the channel |

### Firewall Rule Configuration (Linux `iptables` Example)

```bash
# Allow outbound gRPC to CF-Intelligence coordinator
iptables -A OUTPUT -p tcp --dport 50051 -d coordinator.cf-intelligence.io -j ACCEPT

# Block all other outbound traffic on 50051 (defense-in-depth)
iptables -A OUTPUT -p tcp --dport 50051 -j DROP
```

---

## 2. The 6-Stage Automated Onboarding Pipeline

When a registration request is submitted, [`BankOnboardingService`](file:///c:/Users/Yusuf/Desktop/projects/Privacy-preserving%20cross-bank%20fraud%20detection%20using%20Federated%20Learning/backend/app/application/services/bank_onboarding_service.py) automatically coordinates six discrete provisioning tasks:

```
┌──────────────────────────────────────────────────────────────────────────────────────────────────┐
│                             6-STAGE AUTOMATED ONBOARDING PIPELINE                                │
│                                                                                                  │
│   1. Register Record        2. Issue mTLS X.509       3. Schema Isolation   4. Vault KMS Path    │
│   ┌────────────────────┐   ┌─────────────────────┐   ┌───────────────────┐  ┌──────────────────┐ │
│   │ TenantConfigModel  │──►│ RSA-2048 Keypair    │──►│ CREATE SCHEMA     │─►│ transit/keys/    │ │
│   │ status:            │   │ CN={id}.client...   │   │ tenant_{bank_id}  │  │ tenant_{bank_id} │ │
│   │ PENDING_VERIFY     │   │ SHA-256 Fingerprint │   │ (init_tables)     │  │                  │ │
│   └────────────────────┘   └─────────────────────┘   └───────────────────┘  └──────────────────┘ │
│                                                                                      │           │
│                                              6. Node Activation                      ▼           │
│                                            ┌─────────────────────┐         5. YAML Generation    │
│                                            │ status: ACTIVE      │◄───────────────────────────── │
│                                            │ activated_at: NOW() │         connector_config_yaml │
│                                            │ Anti-TOFU Bound     │         (batch, dp_eps, clip) │
│                                            └─────────────────────┘                               │
└──────────────────────────────────────────────────────────────────────────────────────────────────┘
```

1. **Step 1: Database Registration ([`register_bank`](file:///c:/Users/Yusuf/Desktop/projects/Privacy-preserving%20cross-bank%20fraud%20detection%20using%20Federated%20Learning/backend/app/application/services/bank_onboarding_service.py#L42))**:
   - Validates `bank_id` format (`^[a-zA-Z0-9_-]{3,36}$`) and ISO 3166-1 alpha-2 jurisdiction.
   - Inserts record into `TenantConfigModel` with status `BankStatus.PENDING_VERIFICATION`.
   - Prevents duplicate registration; raises `BankAlreadyExistsError` (HTTP 409 Conflict).
2. **Step 2: mTLS X.509 Certificate Issuance ([`issue_mtls_certificate`](file:///c:/Users/Yusuf/Desktop/projects/Privacy-preserving%20cross-bank%20fraud%20detection%20using%20Federated%20Learning/backend/app/application/services/bank_onboarding_service.py#L84))**:
   - Generates a 2048-bit RSA keypair and X.509 client certificate (`CN={bank_id}.client.cf-intelligence.io`).
   - Computes SHA-256 fingerprint (`SHA256:<hex>`) and 365-day validity window.
   - Updates `cert_fingerprint` and `cert_expires_at` in the database.
3. **Step 3: Anti-TOFU gRPC Fingerprint Binding ([`register_bank_fingerprint`](file:///c:/Users/Yusuf/Desktop/projects/Privacy-preserving%20cross-bank%20fraud%20detection%20using%20Federated%20Learning/backend/app/infrastructure/grpc/servicer.py#L81))**:
   - Authoritatively registers the certificate fingerprint in [`FederatedLearningServicer`](file:///c:/Users/Yusuf/Desktop/projects/Privacy-preserving%20cross-bank%20fraud%20detection%20using%20Federated%20Learning/backend/app/infrastructure/grpc/servicer.py#L89) memory and database fallback.
   - Strictly blocks Trust-On-First-Use (TOFU) race conditions and cross-tenant impersonation.
4. **Step 4: PostgreSQL Engine-Level Schema Provisioning ([`provision_tenant_schema`](file:///c:/Users/Yusuf/Desktop/projects/Privacy-preserving%20cross-bank%20fraud%20detection%20using%20Federated%20Learning/backend/app/application/services/bank_onboarding_service.py#L122))**:
   - Executes DDL to create isolated schema space: `CREATE SCHEMA IF NOT EXISTS tenant_{bank_id}`.
   - Initializes tenant tables (`alerts`, `cases`, `features`, `audit_logs`) within that schema.
5. **Step 5: Vault Transit KMS Key Path Mapping ([`provision_kms_key`](file:///c:/Users/Yusuf/Desktop/projects/Privacy-preserving%20cross-bank%20fraud%20detection%20using%20Federated%20Learning/backend/app/application/services/bank_onboarding_service.py#L133))**:
   - Assigns dedicated encryption key path: `transit/keys/tenant_{bank_id}` for envelope encryption.
6. **Step 6: Connector Config Generation & Activation ([`activate_bank`](file:///c:/Users/Yusuf/Desktop/projects/Privacy-preserving%20cross-bank%20fraud%20detection%20using%20Federated%20Learning/backend/app/application/services/bank_onboarding_service.py#L163))**:
   - Renders customized `connector_config_yaml`.
   - Transitions status to `BankStatus.ACTIVE` with `activated_at = datetime.now(UTC)`.

---

## 3. Step-by-Step CLI Onboarding & Deployment

### Step 1: Issue Registration Request

```bash
curl -X POST https://api.cf-intelligence.io/api/v1/onboarding/register \
  -H "Content-Type: application/json" \
  -d '{
    "bank_id": "bank_alpha",
    "legal_name": "Alpha National Bank Inc.",
    "jurisdiction": "TR",
    "contact_email": "sec-ops@alphabank.com",
    "data_residency_region": "eu-west-1"
  }'
```

#### Onboarding Bundle Response Breakdown

The API returns HTTP 201 Created with [`BankOnboardingBundleResponse`](file:///c:/Users/Yusuf/Desktop/projects/Privacy-preserving%20cross-bank%20fraud%20detection%20using%20Federated%20Learning/backend/app/presentation/routers/onboarding.py#L69):

```json
{
  "bank_id": "bank_alpha",
  "status": "active",
  "legal_name": "Alpha National Bank Inc.",
  "jurisdiction": "TR",
  "contact_email": "sec-ops@alphabank.com",
  "data_residency_region": "eu-west-1",
  "cert_fingerprint": "SHA256:4a8b1c2d3e4f5a6b7c8d9e0f1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f9a0b",
  "mtls_cert_pem": "-----BEGIN CERTIFICATE-----\n...\n-----END CERTIFICATE-----",
  "mtls_key_pem": "-----BEGIN RSA PRIVATE KEY-----\n...\n-----END RSA PRIVATE KEY-----",
  "connector_config_yaml": "bank_id: \"bank_alpha\"\ncoordinator_url: \"https://coordinator.cf-intelligence.io:50051\"\n...",
  "coordinator_endpoint": "https://coordinator.cf-intelligence.io:50051"
}
```

### Step 2: Install Certificates Locally

Secure the certificates on the bank node's filesystem with restrictive permissions:

```bash
mkdir -p /etc/cfi/certs
chmod 700 /etc/cfi/certs

# Save certificate and private key
echo "$MTLS_CERT_PEM" > /etc/cfi/certs/bank_alpha.crt
echo "$MTLS_KEY_PEM" > /etc/cfi/certs/bank_alpha.key

chmod 644 /etc/cfi/certs/bank_alpha.crt
chmod 600 /etc/cfi/certs/bank_alpha.key
```

### Step 3: Configure Connector Daemon

Save the rendered YAML config to `/etc/cfi/config/bank_alpha.yaml`:

```yaml
# CF-Intelligence Bank Client Connector Configuration
bank_id: "bank_alpha"
coordinator_url: "https://coordinator.cf-intelligence.io:50051"
cert_path: "/etc/cfi/certs/bank_alpha.crt"
key_path: "/etc/cfi/certs/bank_alpha.key"
ca_cert_path: "/etc/cfi/certs/ca.crt"
connector_type: "PARQUET"
batch_size: 1000
dp_epsilon: 0.5
clip_norm: 1.0
health_port: 8080
```

### Step 4: Launch the Local Training Daemon

Launch the local FL training client daemon (`cfi-daemon`):

```bash
# Launch daemon process with explicit bank ID and configuration path
cfi-daemon --bank-id bank_alpha --config /etc/cfi/config/bank_alpha.yaml
```

- **Process Lock**: Writes process PID to `storage/daemon.pid` (or `/var/run/cfi/daemon.pid`).
- **Health Check Endpoint**: Exposes lightweight status server at `http://localhost:8080/health`.
- **Graceful Shutdown**: Catches `SIGTERM` / `SIGINT` signals and waits up to 30 seconds for in-flight training rounds to complete before cleanly removing the PID file.

### Step 5: Verify Node Connectivity

Verify node operational status via the CLI tool:

```bash
cfi-cli status --bank-id bank_alpha
```

Expected output:
```text
+---------------+--------------------------+---------+-------------------+
| Bank ID       | Legal Name               | Status  | Schema            |
+---------------+--------------------------+---------+-------------------+
| bank_alpha    | Alpha National Bank Inc. | ACTIVE  | tenant_bank_alpha |
+---------------+--------------------------+---------+-------------------+
```

---

## 🖥️ Web UI Onboarding & Ingestion Studio (`/onboarding`)

Financial institutions can also onboard directly via the browser-based **Bank Node Onboarding Wizard** ([`frontend/src/pages/BankOnboardingPage.tsx`](file:///c:/Users/Yusuf/Desktop/projects/Privacy-preserving%20cross-bank%20fraud%20detection%20using%20Federated%20Learning/frontend/src/pages/BankOnboardingPage.tsx)):

```
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                        WEB UI ONBOARDING WIZARD STEPS (/onboarding)                    │
│                                                                                        │
│  [Step 1: Legal Info] ──► [Step 2: Review] ──► [Step 3: mTLS PKI]                      │
│   - Bank ID                - Compliance Audit   - X.509 Cert Generation                │
│   - Legal Entity Name      - Data Sovereignty   - SHA-256 Fingerprint                  │
│   - Jurisdiction (EU/US/TR)                     - 1-Click Keypair Download             │
│   - Data Residency Region                               │                              │
│                                                         ▼                              │
│  [Step 5: Connection & Ingestion] ◄── [Step 4: Config YAML]                            │
│   - Quorum Health Check                - Auto-generated bank_{id}.yaml                 │
│   - Dataset Ingestion Studio Launch    - Batch size, DP epsilon, clip norm             │
│   - Zero-PII Sanitization & GX Gates                                                   │
└────────────────────────────────────────────────────────────────────────────────────────┘
```

1. **Step 1: Institutional Legal & Regional Profile**: Select bank ID, legal entity name, regulatory jurisdiction (e.g. `EU`, `US`, `UK`, `TR`, `SG`, `JP`), and data residency region (`eu-central-1`, `us-east-1`, `ap-southeast-1`).
2. **Step 2: Compliance Review**: Institutional data sovereignty, zero raw PII transmission guarantees, and regulatory jurisdiction audit checks.
3. **Step 3: Cryptographic mTLS X.509 Credentials**: Displays issued client certificate, private key, and SHA-256 fingerprint with 1-click clipboard copy and PEM download buttons.
4. **Step 4: Bank Daemon Configuration**: Pre-rendered YAML configuration ready for direct download.
5. **Step 5: Node Quorum Activation & Real Dataset Ingestion Studio**:
   - Confirms active quorum status (100% healthy).
   - Launches the **Dataset Ingestion Studio Modal** (`DatasetIngestionStudioModal`):
     - **Drag-and-Drop Ingestion**: Upload transactions via CSV or Parquet.
     - **Client-Side Zero-PII Sanitization**: Validates PANs using Luhn checksum, strips IBAN/TCKN via regex, and tokenizes account identifiers using type-salted HMAC-SHA256.
     - **Great Expectations Contract Gating**: 12 automated checks (schema validation, non-null bounds, range tests, Non-IID Dirichlet $\alpha$ skew, and Kolmogorov-Smirnov drift tests).

---

## 🔒 Security Invariants & Anti-TOFU Defenses

### 1. Anti-TOFU (Trust On First Use) Prevention
In unhardened federated systems, coordinators accept any certificate presented during the initial connection (TOFU). In CF-Intelligence, TOFU is **strictly prohibited**:
- At onboarding time, [`BankOnboardingService.issue_mtls_certificate()`](file:///c:/Users/Yusuf/Desktop/projects/Privacy-preserving%20cross-bank%20fraud%20detection%20using%20Federated%20Learning/backend/app/application/services/bank_onboarding_service.py#L84) binds the issued fingerprint directly in [`FederatedLearningServicer`](file:///c:/Users/Yusuf/Desktop/projects/Privacy-preserving%20cross-bank%20fraud%20detection%20using%20Federated%20Learning/backend/app/infrastructure/grpc/servicer.py#L89).
- When a node connects via `RegisterClient`, the coordinator verifies that the bank node was pre-onboarded. Un-onboarded nodes are immediately rejected (`is_accepted = False`).

### 2. Cross-Tenant Certificate Anti-Spoofing
- If a rogue node (`bank_adversary`) attempts to present a valid certificate fingerprint belonging to another bank (`bank_legit`), [`FederatedLearningServicer.RegisterClient()`](file:///c:/Users/Yusuf/Desktop/projects/Privacy-preserving%20cross-bank%20fraud%20detection%20using%20Federated%20Learning/backend/app/infrastructure/grpc/servicer.py#L153) detects the identity mismatch:
  `"Cross-tenant certificate spoofing rejected: bank 'bank_adversary' presented fingerprint registered to 'bank_legit'"`.

### 3. Database Fallback Verification
- If a coordinator instance restarts, [`_lookup_authoritative_fingerprint()`](file:///c:/Users/Yusuf/Desktop/projects/Privacy-preserving%20cross-bank%20fraud%20detection%20using%20Federated%20Learning/backend/app/infrastructure/grpc/servicer.py#L121) queries the persistent `TenantConfigModel.cert_fingerprint` column in PostgreSQL, restoring the anti-spoofing cache without requiring re-onboarding.

---

## 🛡️ Federated Learning Privacy & Data Isolation

### 1. Multi-Layer Gradient Defense

$$\text{Masked Gradient: } m_u = g_u + \sum_{v > u} s_{u,v} - \sum_{v < u} s_{v,u}$$

- **Secure Aggregation (SecAgg)**: Nodes exchange Diffie-Hellman public keys to generate zero-sum pairwise masks $s_{u,v}$. Upon aggregation, pairwise masks cancel out exactly ($\sum_u m_u = \sum_u g_u$).
- **Differential Privacy (DP)**: Local gradients are clipped to $L_2$ norm threshold $C \le 1.0$, and Gaussian noise calibrated to privacy budget $\epsilon \le 10.0$ is injected. Submissions violating $\epsilon$ bounds are rejected (`REJECTED_EPSILON`).
- **ECDSA Digital Signatures**: Each gradient tensor is compressed via `zlib` and digitally signed by the node's HSM / PKI private key:
  $$\text{Signature} = \text{Sign}_{K_{\text{private}}}\Big(\text{round-id} \mathbin{\Vert} \text{bank-id} \mathbin{\Vert} \text{SHA-256}(\text{compressed-gradient})\Big)$$

### 2. PostgreSQL Engine-Level Schema Isolation
- **Dedicated Schema**: `CREATE SCHEMA IF NOT EXISTS tenant_{bank_id}` ensures physical table separation.
- **Role Isolation**: A dedicated user `tenant_{bank_id}_role` is granted access strictly to `tenant_{bank_id}` with zero privileges on other schemas. Cross-schema queries trigger PostgreSQL engine error `SQLState 42501 (insufficient_privilege)`.
- **Search Path Enforcement**: Every tenant request executes `SET search_path TO tenant_{bank_id}, public`.

### 3. 90-Day Certificate Rotation Lifecycle
- **Automated Warning Threshold**: Automated background workers trigger alerts when `< 30 days` remain before certificate expiration (`check_cert_expiry`).
- **Zero-Downtime Hot Swapping**: [`POST /api/v1/onboarding/banks/{bank_id}/rotate-cert`](file:///c:/Users/Yusuf/Desktop/projects/Privacy-preserving%20cross-bank%20fraud%20detection%20using%20Federated%20Learning/backend/app/presentation/routers/onboarding.py#L347) issues a fresh keypair and updates the coordinator registry in-memory.
- **Manual CLI Trigger**:
  ```bash
  cfi-cli rotate-certs --bank-id bank_alpha
  ```

---

## 🌐 Onboarding REST API Reference

All onboarding endpoints are served under prefix `/api/v1/onboarding` in [`backend/app/presentation/routers/onboarding.py`](file:///c:/Users/Yusuf/Desktop/projects/Privacy-preserving%20cross-bank%20fraud%20detection%20using%20Federated%20Learning/backend/app/presentation/routers/onboarding.py):

| Method | Endpoint | Request Body | Response Schema | Description |
|:---|:---|:---|:---|:---|
| `POST` | `/register` | `BankRegisterRequest` | `BankOnboardingBundleResponse` | Execute automated 6-step onboarding pipeline (status 201 Created). Returns mTLS certs, key, fingerprint, and YAML config. |
| `GET` | `/banks` | None | `list[BankStatusResponse]` | List all registered bank nodes with jurisdiction, status, cert fingerprint, and vault key path. |
| `GET` | `/banks/{bank_id}/status` | None | `BankStatusResponse` | Retrieve detailed status for a specific bank node. |
| `POST` | `/banks/{bank_id}/rotate-cert` | None | `CertRotationResponse` | Rotate mTLS certificate and private key for an active bank node. |

---

## 🧪 Automated Unit Test Suite Matrix

The bank onboarding pipeline and security invariants are verified by the automated unit test suite in [`backend/tests/unit/test_bank_onboarding.py`](file:///c:/Users/Yusuf/Desktop/projects/Privacy-preserving%20cross-bank%20fraud%20detection%20using%20Federated%20Learning/backend/tests/unit/test_bank_onboarding.py).

### Test Execution Command

```bash
pytest backend/tests/unit/test_bank_onboarding.py -v
```

### Verified Test Results (8 Passed in 7.15s)

| Test Function | Target Component / Layer | Assertion / Behavior Verified | Status |
|:---|:---|:---|:---:|
| `test_register_bank_creates_db_record` | `BankOnboardingService.register_bank` | Inserts `TenantConfigModel` in `pending_verification` status; schema unprovisioned. | `PASSED` |
| `test_full_onboarding_pipeline_sets_active` | Full Service Pipeline | Complete transition to `ACTIVE`, schema provisioned, Vault transit KMS path mapped, X.509 RSA-2048 keypair cryptographically verified. | `PASSED` |
| `test_two_banks_receive_distinct_x509_certificates` | Cryptographic Cert Engine | Distinct onboarding calls produce distinct RSA public/private keypairs, distinct modulus $n$, and distinct SHA-256 fingerprints. | `PASSED` |
| `test_grpc_cross_tenant_certificate_spoofing_rejected` | `FederatedLearningServicer` Anti-Spoofing | Rejects cross-tenant impersonation when node presents another bank's fingerprint; rejects altered fingerprints. | `PASSED` |
| `test_tofu_race_condition_rejected_for_unonboarded_bank` | Anti-TOFU Security Barrier | Un-onboarded bank nodes attempting to register via gRPC before onboarding are strictly rejected. Legitimate registration succeeds only after onboarding. | `PASSED` |
| `test_duplicate_bank_id_rejected` | Service Validation | Attempting to register an already existing `bank_id` raises `BankAlreadyExistsError`. | `PASSED` |
| `test_connector_config_contains_required_fields` | Configuration Generator | Renders valid YAML string containing `bank_id`, `coordinator_url`, `cert_path`, and `key_path`. | `PASSED` |
| `test_onboarding_endpoint_returns_bundle` | FastAPI Presentation Router | `POST /api/v1/onboarding/register` returns 201 Created with bundle; duplicate returns 409; `/banks` and `/banks/{id}/status` return 200 OK. | `PASSED` |

---

## 🔧 Operational Troubleshooting Matrix

| Issue Code / Error Message | Root Cause | Remediation Procedure |
|:---|:---|:---|
| `UNAUTHENTICATED: Certificate expired` | mTLS client cert 365-day TTL has elapsed | Execute `cfi-cli rotate-certs --bank-id <id>` or `POST /api/v1/onboarding/banks/{id}/rotate-cert`. |
| `PERMISSION_DENIED: Bank not active` | Registration in `PENDING_VERIFICATION` status | Check coordinator database or trigger `/api/v1/onboarding/register` pipeline completion. |
| `UNAVAILABLE: Name resolution failed` | Outbound TCP port 50051 blocked by firewall | Verify firewall allowlist permits outbound traffic to `coordinator.cf-intelligence.io:50051`. |
| `SQLState 42501 (insufficient_privilege)` | Process attempting cross-tenant database access | Ensure `get_tenant_session(bank_id)` sets `search_path TO tenant_{bank_id}, public`. |
| `gRPC Registration Rejected (is_accepted=False)` | Anti-TOFU barrier triggered or cert spoofing detected | Verify node was properly onboarded via API and presents the authentic fingerprint issued during onboarding. |
