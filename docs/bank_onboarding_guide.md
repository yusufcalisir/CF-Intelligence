# 🏦 Bank Node Automated Onboarding & Operations Guide

This guide details the end-to-end process for onboarding a new financial institution node to the **Collaborative Fraud Intelligence (CF-Intelligence)** platform.

---

## 1. Prerequisites

Before initiating node registration, the institution's IT/Security team must verify:
- **Outbound Network Access:** Outbound TCP port `50051` (gRPC mTLS) open to `coordinator.cf-intelligence.io`.
- **Admin Access:** API key or administrative credentials to issue onboarding calls to `/api/v1/onboarding/register`.
- **System Requirements:** Python 3.12+, Docker/Kubernetes container runtime, and at least 4 GB RAM / 2 vCPUs for local training.

---

## 1a. Network Requirements

> [!IMPORTANT]
> All CF-Intelligence bank→coordinator communication is **mandatory mTLS over gRPC**.
> Plain HTTP connections and TLS 1.2 are **refused at the transport layer** — there is no fallback.

| Requirement | Detail |
|---|---|
| **Protocol** | gRPC over TLS 1.3 only (TLS 1.2 rejected) |
| **Port** | TCP `50051` outbound from bank network to coordinator |
| **Authentication** | Mutual TLS — both client and server present certificates |
| **Client cert CN** | Must match `{bank_id}.client.cf-intelligence.io` (issued by onboarding API) |
| **HTTP fallback** | None — insecure channels are rejected at the server interceptor level |
| **Coordinator FQDN** | `coordinator.cf-intelligence.io` — add to firewall allowlist |
| **Cert rotation** | gRPC client auto-detects cert file changes and recycles the channel |

### Firewall Rule (example — adapt to your environment)

```bash
# Allow outbound gRPC to CF-Intelligence coordinator
iptables -A OUTPUT -p tcp --dport 50051 -d coordinator.cf-intelligence.io -j ACCEPT

# Block all other outbound on 50051 (defence-in-depth)
iptables -A OUTPUT -p tcp --dport 50051 -j DROP
```

---

## 2. Step 1: API Registration

Issue a registration request to the central coordinator admin endpoint:

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

### Response Payload Breakdown

The response returns the complete **Onboarding Bundle**:
- `bank_id`: Confirmed unique bank identifier.
- `cert_fingerprint`: SHA-256 fingerprint of the issued mTLS certificate.
- `mtls_cert_pem`: Mutual TLS client certificate (PEM format).
- `mtls_key_pem`: Private key for mTLS client authentication (PEM format).
- `connector_config_yaml`: Pre-rendered YAML configuration for the local bank daemon.

---

## 3. Step 2: Certificate Installation

Save the returned certificates securely on the bank's local node:

```bash
mkdir -p /etc/cfi/certs
chmod 700 /etc/cfi/certs

# Save certificate and key
echo "$MTLS_CERT_PEM" > /etc/cfi/certs/bank_alpha.crt
echo "$MTLS_KEY_PEM" > /etc/cfi/certs/bank_alpha.key

chmod 600 /etc/cfi/certs/bank_alpha.key
```

---

## 4. Step 3: Connector Config

Save the `connector_config_yaml` to `/etc/cfi/config/bank_alpha.yaml`:

```yaml
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

---

## 5. Step 4: Start the Daemon

Launch the local FL client training daemon process (`cfi-daemon`):

```bash
# Launch daemon process with explicit bank ID and configuration path
cfi-daemon --bank-id bank_alpha --config ~/.cfi/config/bank_alpha.yaml
```

### Operational Flags & Configuration Overview:
- `--bank-id`: Unique consortium bank identifier.
- `--config`: Path to local YAML daemon configuration file (`~/.cfi/config/{bank_id}.yaml` or `/etc/cfi/config/{bank_id}.yaml`).
- **Process Lock**: Writes process PID to `storage/daemon.pid` (or `/var/run/cfi/daemon.pid`).
- **Health Check Endpoint**: Exposes lightweight status server at `http://localhost:8080/health`.
- **Graceful Shutdown**: Catches `SIGTERM` / `SIGINT` signals and waits up to 30 seconds for in-flight training rounds to complete before removing the PID file.

---

## 6. Step 5: Verify Connection

Check the node operational status:

```bash
cfi-cli status --bank-id bank_alpha
```

Expected output:
```text
+---------------+------------------------+---------+-------------------+
| Bank ID       | Legal Name             | Status  | Schema            |
+---------------+------------------------+---------+-------------------+
| bank_alpha    | Alpha National Bank    | ACTIVE  | tenant_bank_alpha |
+---------------+------------------------+---------+-------------------+
```

---

## 5b. Web UI Onboarding & Dataset Ingestion Studio (`/onboarding`)

In addition to CLI automation, financial institutions can onboard directly via the browser-based **Bank Node Onboarding Wizard**:

1. **Step 1: Institutional Legal & Regional Profile**: Select bank ID, legal entity name, regulatory jurisdiction (e.g. `TR`, `EU`, `US`), and data residency region (`eu-west-1`, `us-east-1`, `ap-southeast-1`).
2. **Step 2: Review Registration Details**: Institutional data sovereignty and privacy verification check.
3. **Step 3: Cryptographic mTLS X.509 Credentials**: Automated browser-side certificate and private key generation with SHA-256 fingerprint validation.
4. **Step 4: Bank Daemon Configuration**: Auto-generated YAML configuration (`bank_{id}.yaml`) ready for 1-click download.
5. **Step 5: Node Quorum Activation & Initial Ingestion**: Confirms active quorum status (100% healthy) and provides a direct launcher for the **Real Dataset Ingestion Studio**:
   - **Drag-and-Drop Ingestion**: Upload transactions via CSV or Parquet.
   - **Client-Side Zero-PII Sanitization**: Validates PANs using the Luhn checksum, strips IBAN/TCKN via regex, and tokenizes account identifiers with type-salted HMAC-SHA256.
   - **Great Expectations Contract Gating**: 12 automated checks (schema validation, non-null bounds, range tests, Non-IID Dirichlet $\alpha$ skew, and KS drift).

---

## 6a. How Gradient Submission Works

> [!NOTE]
> Gradient updates submitted during a federated learning round are protected by three layers of defence:
> **Secure Aggregation (SecAgg)**, **Opacus Differential Privacy (DP)**, and **HSM/PKI ECDSA Digital Signatures**.

1. **Secure Aggregation (SecAgg) Masking**:
   - Each participating bank node generates pairwise random zero-sum masks $s_{u,v}$ with all other online consortium members.
   - The local gradient vector $g_u$ is masked: $m_u = g_u + \sum_{v > u} s_{u,v} - \sum_{v < u} s_{v,u}$.
   - Upon coordinator aggregation, pairwise masks cancel out exactly ($\sum_u m_u = \sum_u g_u$), revealing only the aggregate model update while guaranteeing individual bank gradient privacy.

2. **Differential Privacy ($\epsilon, \delta$) Noise**:
   - Local gradients are clipped to $L_2$ norm threshold $C$ (e.g., $1.0$).
   - Gaussian noise calibrated to privacy budget $\epsilon$ is added. The coordinator enforces $\epsilon \le 10.0$ per round. Submissions exceeding this limit are rejected with `REJECTED_EPSILON`.

3. **Cryptographic Payload Compression & Signing**:
   - The masked gradient tensor is compressed using `zlib`.
   - The node signs the payload digest using its HSM / PKI private key (ECDSA P-256 / RSA-PSS):
     $$\text{Signature} = \text{Sign}_{K_{\text{private}}}\Big(\text{round-id} \mathbin{\Vert} \text{bank-id} \mathbin{\Vert} \text{SHA-256}(\text{compressed-gradient})\Big)$$
   - The coordinator verifies the signature via `SignatureVerifier` before storing in `gradient_submissions` and logging to the `ImmutableAuditChain`.

4. **Quorum Aggregation**:
   - The coordinator accumulates validated submissions until reaching the round quorum threshold (e.g. 3 banks).
   - Once quorum is satisfied, global model parameter aggregation is triggered.

---

## 6b. Supported Core Banking Integration Formats

The CF-Intelligence platform natively ingests data from core banking systems using the following standardized messaging standards and API specs:

### 1. ISO 20022 Financial Messaging (MX)
- **`pacs.008.001.08`**: Financial Institution Customer Credit Transfer. Validated against XSD schema `pacs.008.001.08.xsd`.
- **`camt.053.001.08`**: Bank-to-Customer Statement. Validated against XSD schema `camt.053.001.08.xsd`.
- **`pain.001.001.08`**: Customer Credit Transfer Initiation. Validated against XSD schema `pain.001.001.08.xsd`.
- **`pacs.002.001.10`**: Payment Status Report.

### 2. Legacy SWIFT Financial Messaging (MT)
- **`MT103`**: Single Customer Credit Transfer text payload parser.

### 3. Open Banking & PSD2 REST APIs
- **Berlin Group NextGenPSD2**: Version 1.3 Account Information Service (AIS) and Payment Initiation Service (PIS).
- **UK Open Banking**: Read/Write Data API Specification v3.1.
- **Security & Lifecycle**: OAuth2 Client Credentials grant with automatic token refresh (< 5 minutes TTL) and HTTP 429 rate limit backoff (`Retry-After`).

---

## 6c. Data Isolation Guarantee

> [!IMPORTANT]
> The CF-Intelligence platform enforces **PostgreSQL Engine-Level Schema Isolation** (SOC2 Type II / PCI-DSS v4.0 compliant).

1. **Dedicated PostgreSQL Schema (`tenant_{bank_id}`)**:
   - Upon registration, `TenantProvisioner` executes DDL statements to create an isolated schema space: `CREATE SCHEMA IF NOT EXISTS tenant_{bank_id}`.
   - All tenant database tables (`alerts`, `cases`, `features`, `audit_logs`) reside exclusively within this schema.

2. **Dedicated Database Role (`tenant_{bank_id}_role`)**:
   - A dedicated database user role is provisioned: `CREATE ROLE tenant_{bank_id}_role WITH NOINHERIT LOGIN`.
   - Privileges are granted exclusively for `tenant_{bank_id}`. The role has **zero permissions** on other bank schemas (`tenant_bank_b`, `tenant_bank_c`).
   - Cross-bank SQL queries are blocked directly at the database engine parser level, raising a `PermissionError` / `SQLState 42501 (insufficient_privilege)`.

3. **Session Search Path Isolation**:
   - Every request session initialized via `get_tenant_session(bank_id)` automatically executes `SET search_path TO tenant_{bank_id}, public`.

---

## 6d. Certificate Rotation Lifecycle

> [!NOTE]
> All mTLS X.509 client certificates and Vault Transit KMS keys are managed on a **90-Day Rotation Schedule**.

1. **Automated Warning Threshold**:
   - Automated maintenance workers trigger warnings when `< 30 days` remain before certificate expiry (`check_cert_expiry`).
2. **Zero-Downtime Hot Swapping**:
   - `MTLSManager.rotate_cert(bank_id)` issues fresh X.509 keypairs via Vault PKI Engine (`POST /v1/pki/issue/bank-client`).
3. **Manual CLI Trigger**:
   - Administrators can manually trigger certificate rotation at any time:
     ```bash
     cfi-cli rotate-certs --bank-id <id>
     ```

---

## 7. Troubleshooting

| Issue | Root Cause | Resolution |
|---|---|---|
| `UNAUTHENTICATED: Certificate expired` | Cert TTL elapsed | Run `cfi-cli rotate-certs --bank-id <id>` |
| `PERMISSION_DENIED: Bank not active` | Registration pending verification | Contact coordinator admin to activate node |
| `UNAVAILABLE: Name resolution failed` | Port 50051 blocked | Verify firewall rules for TCP 50051 |
| `QuorumNotMetError` | Insufficient participating banks | Wait for additional consortium members to join round |
