# Enterprise Cloud Production Infrastructure & Live Orchestration Blueprint (2026 Edition)

---

## 1. Executive Overview: From Single-Node Compose to Multi-Region Cloud Mesh

While local developers use `docker-compose.yml` for offline testing and `docker-compose.multinode.yml` for distributed multi-bank testbed validation, **production deployments of CF-Intelligence operate on a multi-cloud, multi-region, auto-scaling Kubernetes (EKS / AKS / GKE) and managed infrastructure mesh**.

The production cloud topology features an active-passive multi-region architecture designed for zero data loss, sub-100ms real-time fraud scoring SLAs, high-availability federated training rounds, and automated regional disaster recovery:

```
                                    [ Global Route53 / Cloudflare DNS Latency Routing ]
                                                            │
                                  ┌─────────────────────────┴─────────────────────────┐
                                  │                                                   │
                                  ▼                                                   ▼
            ┌───────────────────────────────────────────┐       ┌───────────────────────────────────────────┐
            │       PRIMARY REGION: EU-CENTRAL-1        │       │       STANDBY REGION: EU-WEST-1           │
            │               (ACTIVE)                    │       │               (PASSIVE)                   │
            ├───────────────────────────────────────────┤       ├───────────────────────────────────────────┤
            │ • Cloudflare Edge WAF & L7 Rate Limiting  │       │ • Cloudflare Edge WAF & L7 Rate Limiting  │
            │ • EKS / AKS / GKE Cluster (HPA 3-30 Pods) │       │ • EKS / AKS / GKE Cluster (Min 2 Standby) │
            │ • Ingress-NGINX + Strict mTLS 1.3         │       │ • Ingress-NGINX + Strict mTLS 1.3         │
            │ • Strimzi Kafka (ISO 20022 pacs.008 feed) │ ───►  │ • MirrorMaker 2 Kafka Cross-Region Sync   │
            │ • Multi-AZ PostgreSQL 16 (Tenant Schemas) │ ───►  │ • Aurora / Azure / CloudSQL Global Sync   │
            │ • Redis HA Sentinel (Namespaced Keys)     │ ───►  │ • Redis Standby Cluster Replication       │
            │ • HashiCorp Vault PKI / HSM / Transit KMS │       │ • HashiCorp Vault Standby Replicator      │
            │ • Prometheus / Loki / OTel / Grafana      │       │ • Prometheus Standby Scraper              │
            │ • SIEM Forwarder (Syslog RFC 5424 / ECS)  │       │ • Standby SIEM Telemetry Bridge           │
            └───────────────────────────────────────────┘       └───────────────────────────────────────────┘
```

---

## 2. Multi-Cloud Infrastructure as Code (IaC) Stack (`deployments/terraform/`)

The platform provides modular, battle-tested Terraform modules ensuring turnkey deployment across the three major hyperscalers and edge security providers.

```
deployments/terraform/
├── aws/                   # AWS multi-AZ VPC, EKS 1.30, Aurora PostgreSQL, MSK Kafka, KMS
├── azure/                 # Azure VNet, AKS with Calico CNI, Flexible Server, Key Vault
├── gcp/                   # GCP VPC, GKE Private Cluster, Cloud SQL, Cloud KMS KeyRing
├── cloudflare/            # Cloudflare Layer 1 Edge WAF, L7 Rate Limiting, TLS 1.3 Strict
└── modules/               # Reusable cloud-agnostic building blocks
    ├── db/                # PostgreSQL relational persistence & parameter encryption
    ├── k8s/               # Kubernetes cluster topology & node group configurations
    └── security/          # Network security groups, firewalls, and IAM role bindings
```

### 2.1. AWS Terraform Blueprint (`deployments/terraform/aws/`)
* **VPC & Subnet Topology**: Multi-AZ dual-region deployment (`eu-central-1a/b/c` and `eu-west-1a/b/c`) featuring public ingress subnets, private application worker subnets, and database-isolated subnets.
* **EKS Managed Node Groups**: Kubernetes 1.30 with GPU acceleration (`g5.2xlarge` with NVIDIA A10G for local GNN embedding training) and confidential computing instances (`m6i.metal` for Intel SGX v2 enclaves).
* **Managed Data Planes**:
  * **AWS MSK / Strimzi Kafka**: High-throughput distributed partition streaming for ISO 20022 message ingestion (`pacs.008`, `camt.053`) with TLS client authentication.
  * **AWS Aurora Multi-AZ PostgreSQL**: Multi-tenant database engine enforcing strict schema isolation (`tenant_<id>.*`) and read replicas.
  * **AWS ElastiCache Redis**: Redis Sentinel HA with TLS encryption in transit and per-tenant key namespacing (`cfi:tenant:<bank_id>:*`).
* **Cryptographic Keys**: Dedicated AWS KMS customer-managed keys (`aws_kms_key.cfi`) for EKS secrets envelope encryption.
* **Verification Suite**: `python -m pytest backend/tests/unit/test_terraform_templates.py` (27/27 unit tests pass).

### 2.2. Microsoft Azure Terraform Blueprint (`deployments/terraform/azure/`)
* **VNet & Security Perimeter**: Dedicated Virtual Network (`10.0.0.0/16`) with subnet segregation and Network Security Groups (`azurerm_network_security_group.cfi`) blocking inter-bank lateral movement.
* **Azure Kubernetes Service (AKS)**: AKS 1.30 cluster with Calico network policies, managed identities, and dedicated bank node pools (`azurerm_kubernetes_cluster_node_pool.bank_nodes`).
* **Managed Data Planes**: Azure Database for PostgreSQL Flexible Server with private endpoints, Azure Event Hubs Kafka streaming, and Azure Key Vault (`azurerm_key_vault.cfi`).

### 2.3. Google Cloud Platform (GCP) Blueprint (`deployments/terraform/gcp/`)
* **VPC & Shielded Compute**: Custom VPC network with private Google access and Cloud NAT for outbound container image updates.
* **Google Kubernetes Engine (GKE)**: Private GKE cluster with Workload Identity enabled, shielded GKE nodes, and node auto-repair.
* **Managed Data Planes**: Cloud SQL for PostgreSQL with private IP and Cloud KMS KeyRing (`google_kms_key_ring.cfi`) for envelope encryption.

### 2.4. Cloudflare Edge Perimeter Defense (`deployments/terraform/cloudflare/`)
* **Layer 1 DDoS Mitigation**: Global volumetric L3/L4 attack absorption on Cloudflare's Anycast network.
* **L7 WAF & Bot Screening (`waf_rules.tf`)**:
  * OWASP Top 10 rule enforcement, SQLi / XSS payload blocking, and suspicious client challenges.
  * Bot fight mode and browser integrity checks on inference paths (`/api/v1/predict*`, `/v1/inference/score`).
* **L7 Rate Limiting (`rate_limiting.tf`)**:
  * Global API rate limit: 60 requests / 10s per IP.
  * Heavy ML inference endpoint rate limit: 20 requests / 60s per IP.
* **TLS 1.3 Strict & Header Forwarding**:
  * Complete end-to-end encryption with HSTS enabled.
  * Injects `CF-Connecting-IP` consumed natively by FastAPI's `DDoSProtectionMiddleware` and `slowapi` rate limiter.

---

## 3. Kubernetes Packaging, Helm Architecture & GitOps Delivery

The platform supports both a modular microservices deployment model and a unified bank-node deployment model:

```
deployments/helm/
├── cfi-platform/          # Unified bank-node & aggregator production chart
│   ├── templates/
│   │   ├── aggregator-deployment.yaml     # Central FL aggregator
│   │   ├── bank-node-deployment.yaml      # Institutional bank node daemon
│   │   ├── coordinator-deployment.yaml    # FL coordinator service
│   │   ├── fraud-alert-deployment.yaml    # Real-time fraud alert engine
│   │   ├── identity-graph-deployment.yaml # GNN streaming identity graph
│   │   ├── ingress.yaml                   # TLS ingress definitions
│   │   ├── service.yaml                   # ClusterIP service definitions
│   │   ├── hpa.yaml                       # Horizontal pod autoscalers
│   │   ├── hpa-and-netpol.yaml            # Combined HPA & network policies
│   │   ├── network-policy.yaml            # Zero-trust inter-pod isolation
│   │   └── pod-disruption-budget.yaml     # PDBs enforcing rolling update SLA
│   ├── Chart.yaml                         # Chart metadata (SemVer v2)
│   └── values.yaml                        # Production configuration values
│
└── cfi-platform-root/     # Microservices root umbrella chart
    └── templates/                         # Gateway, Frontend, Coordinator, Graph services
```

### 3.1. Horizontal Pod Autoscaling (`HPA`) & Pod Disruption Budgets (`PDB`)
* **Horizontal Pod Autoscaler (HPA)**:
  * Scales `cfi-scoring-engine` and `cfi-fraud-alert` pods dynamically based on CPU utilization ($>70\%$) and active request throughput ($>250\text{ req/sec/pod}$ via KEDA Prometheus metrics).
  * Scale range: Minimum $3$ pods, maximum $30$ pods per region.
* **Pod Disruption Budgets (PDB)**:
  * `cfi-release-coordinator-pdb` and `cfi-release-fraud-alert-pdb` enforce `minAvailable: 1`, guaranteeing continuous quorum availability during rolling cluster node upgrades.

### 3.2. Zero-Trust Kubernetes Network Policies (`NetworkPolicy`)
* **`cfi-release-bank-node-isolation`**: Restricts bank node pods from establishing cross-tenant network sockets.
* **`cfi-release-coordinator-ingress`**: Permits ingress traffic strictly on authorized gRPC/mTLS port `50051` and HTTP port `8000`.
* **`cfi-release-default-deny-cross-namespace`**: Blocks unauthorized lateral communication across institutional namespaces.

### 3.3. GitOps Continuous Delivery & Zero-Downtime Deployment
* **ArgoCD GitOps Manifest (`deployments/argocd/application.yaml`)**:
  * Automated Git-driven synchronization (`syncPolicy: automated` with `prune: true` and `selfHeal: true`).
* **Zero-Downtime Blue/Green Strategy (`docs/zero_downtime_upgrade_strategy.md`)**:
  * Argo Rollouts blue/green traffic shifting with automated rollback on error budget spikes.
  * Verified by `backend/tests/unit/test_zero_downtime_deployment.py`.

### 3.4. Authentic Rendered Manifest Dry-Run Validation
* **Dry-Run Tool**: `python scripts/validate_k8s_manifests.py`
* **Validation Standard**: Renders Go-templates into fully substituted Kubernetes YAML manifests and validates them against authentic Kubernetes API discovery schemas.
* **Result**: 16/16 resources validated cleanly (Deployments, Services, HPAs, PDBs, NetworkPolicies, Ingress).

---

## 4. High Availability, Disaster Recovery & High-Security Air-Gap Packaging

### 4.1. Multi-Region Active-Passive DR & Coordinator Failover Engine (Phase 24)
* **Architecture**: Active-passive coordinator pairing between Primary (`eu-central-1`) and Standby (`eu-west-1`).
* **Heartbeat & Promotion**:
  * Standby node polls primary coordinator heartbeat at 15-second intervals.
  * Automatic standby promotion triggers if primary fails 3 consecutive heartbeats (45-second threshold).
  * Split-brain fencing blocks stale primary nodes upon recovery.
* **SLA Commitments**: **RPO < 15 minutes**, **RTO < 1 hour**.
* **Engine Implementation**: `backend/app/infrastructure/disaster_recovery/region_failover.py` and `backend/app/domain/dr_coordinator.py`.
* **Chaos Testing**: `backend/app/infrastructure/disaster_recovery/chaos_dr_drill.py` and `backend/tests/unit/test_disaster_recovery_failover.py`.

### 4.2. Automated Backup Verification & Point-in-Time Recovery (Phase 25)
* **Point-in-Time Recovery (PITR)**: Continuous PostgreSQL Write-Ahead Log (WAL) archiving combined with daily base snapshots.
* **Automated Integrity Probes (`backend/app/infrastructure/disaster_recovery/backup_verifier.py`)**:
  * The `BackupVerifier` and `BackupRecord` domain model validate checksum integrity across encrypted model checkpoint vault archives.
  * Executes automated restoration drills verifying database schema parity, tenant isolation boundaries, and RPO compliance.
  * Verified by unit test suite `backend/tests/unit/test_backup_verifier.py`.
* **Technical Specification**: `docs/backup_verification_spec.md`.

### 4.3. Edge WAF & Offline Air-Gapped Datacenter Distribution Bundle (Phase 32)
* **High-Security Banking Requirements**: Designed for tier-1 financial institutions with air-gapped, zero-internet core banking datacenters.
* **Air-Gapped Distribution Bundler (`backend/app/infrastructure/deployment/airgap_installer.py`)**:
  * The `AirGapBundleBuilder` packages container images (`cfi-backend`, `cfi-frontend`, `cfi-gateway`, PostgreSQL, Redis), Helm charts, and Python SDK packages into an offline distribution archive.
  * Generates SHA-256 integrity checksum manifests (`airgap_manifest.json`) verifying cryptographic authenticity before offline installation.
* **Perimeter Inspection**: Strict payload validation and IP whitelist airgap filtering implemented in `backend/app/infrastructure/security/perimeter_waf.py` and verified by `backend/tests/unit/test_perimeter_airgap.py`.
* **Technical Guide**: `docs/airgapped_deployment_guide.md`.

---

## 5. Enterprise Security & Hardware-Rooted Cryptography Infrastructure

```
┌───────────────────────────────────────────────────────────────────────────────────┐
│                      ENTERPRISE SECURITY & CRYPTOGRAPHY STACK                     │
├──────────────────────────────┬────────────────────────────────────────────────────┤
│ HashiCorp Vault PKI Engine   │ Dynamic X.509 mTLS 1.3 cert issuance & rotation    │
├──────────────────────────────┼────────────────────────────────────────────────────┤
│ Hardware Security Module     │ PKCS#11 hardware-backed signing for model weights  │
├──────────────────────────────┼────────────────────────────────────────────────────┤
│ Multi-Tenant Transit KMS     │ Per-tenant cryptographic keys (tenant_<id>)        │
├──────────────────────────────┼────────────────────────────────────────────────────┤
│ Confidential Computing (TEE) │ Intel SGX v2 enclaves / AWS Nitro for SecAgg       │
├──────────────────────────────┼────────────────────────────────────────────────────┤
│ Post-Quantum Cryptography    │ NIST FIPS 203 (ML-KEM-768) & FIPS 204 (ML-DSA-3)   │
├──────────────────────────────┼────────────────────────────────────────────────────┤
│ Layer-2 Web3 Settlement      │ Chainlink CCIP & EVM cross-chain incentive bridge  │
└──────────────────────────────┴────────────────────────────────────────────────────┘
```

* **Zero-Trust PKI (`scripts/init_vault_pki.py`)**: Automated mTLS 1.3 certificate authority issuing short-lived X.509 client/server certificates for all inter-node and inter-bank gRPC communications.
* **HSM Model Signing (`backend/tests/unit/test_hsm_signer.py`)**: Cryptographic signature validation ensuring global models cannot be tampered with in transit.
* **Tenant KMS Segregation (`backend/app/infrastructure/security/tenant_kms.py`)**: Individual tenant data encrypted with dedicated keys residing under Vault path `cfi/tenant/<tenant_id>/key`.
* **Post-Quantum Security (`backend/app/infrastructure/security/pqc_secagg_driver.py`)**: Hardens secure aggregation against quantum computing threats using lattice-based cryptography (NIST FIPS 203 ML-KEM / Kyber-768 and FIPS 204 ML-DSA / Dilithium-3).
* **Layer-2 Settlement Bridge (`backend/app/infrastructure/security/layer2_crosschain_bridge.py`)**: Multi-bank consortium incentive distribution over smart contracts with Gnosis Safe multi-sig governance.

---

## 6. End-to-End Live Observability, Telemetry & SIEM Integration

### 6.1. OpenTelemetry (OTel) Distributed Tracing Engine
Every transaction scoring and federated training request is instrumented with W3C Trace Context headers (`traceparent`, `tracestate`), providing unified span correlation across distributed microservices:

1. **Real-Time Scoring Latency Budget ($<100\text{ms}$ SLA)**:
   $$\text{API Gateway } (1.2\text{ms}) \;\longrightarrow\; \text{GNN Inference } (8.4\text{ms}) \;\longrightarrow\; \text{9-Signal Calibration } (3.6\text{ms}) \;\longrightarrow\; \text{Response } (<14.2\text{ms total})$$
2. **6-Stage Federated Training Lifecycle Tracing**:
   `ingest_transaction` $\to$ `feature_store_aggregation` $\to$ `local_pytorch_training` $\to$ `grpc_mtls_transmit` $\to$ `central_parameter_aggregation` $\to$ `model_registry_save`.
3. **Jaeger Distributed Tracing UI**: Pre-configured Jaeger UI integration (`monitoring/jaeger-ui-config.json`).
4. **Implementation**: `backend/app/infrastructure/telemetry/otel_tracer.py`, verified by `backend/tests/unit/test_otel_tracer.py`.

### 6.2. Prometheus Metrics & Alerting Pipeline
Prometheus server listens on `:9090` and scrapes metrics across all platform services:

| Scrape Job | Target Endpoint | Scraping Interval | Purpose |
| :--- | :--- | :--- | :--- |
| **`cfi-gateway`** | `gateway:8000/metrics/` | 15s | Ingress traffic, API latency histograms, rate limits |
| **`cfi-fl-coordinator`** | `fl-coordinator:8001/metrics/` | 15s | Federated round status, gradient norms, Byzantine drops |
| **`cfi-identity-graph`** | `identity-graph:8002/metrics/` | 15s | GNN embedding throughput, graph node/edge counts |
| **`cfi-fraud-alert`** | `fraud-alert:8003/metrics/` | 15s | Alert volume, SAR filings, investigation queue latency |
| **`prometheus`** | `localhost:9090/metrics` | 15s | Prometheus self-monitoring |

* **Alertmanager Integration**: Connected to `alertmanager:9093` (`monitoring/alertmanager/config.yml`) evaluating alert rules in `monitoring/prometheus/alert_rules.yml`.
* **Key Prometheus Metrics**:
  * `cfi_scoring_latency_seconds_bucket` (p50, p95, p99 latency histograms).
  * `cfi_fl_round_gradient_norm` and `cfi_dp_epsilon_consumed_total`.
  * `cfi_active_bank_nodes_gauge` and `cfi_byzantine_rejections_total`.

### 6.3. Grafana Production Dashboards & Automated Provisioning
Dashboards are provisioned automatically via `monitoring/grafana/provisioning/`:

* **`monitoring/grafana/dashboards/cfi-overview.json`**: Executive high-level fraud prevention metrics, transaction volume, and cost savings.
* **`monitoring/grafana/dashboards/cfi_operational_dashboard.json`**: Real-time service latencies, container CPU/memory utilization, and Kafka partition consumer lag.
* **`monitoring/grafana/dashboards/cfi_privacy_budget_dashboard.json`**: Differential Privacy $(\alpha, \varepsilon)$ budget exhaustion and gradient signal-to-noise ratio (SNR).
* **`deployments/grafana/dashboards/fl_consortium_overview.json`**: Active federated consortium round progress, member node statuses, and convergence curves.
* **`deployments/grafana/dashboards/privacy_security_audit.json`**: mTLS handshake metrics, cryptographic audit trails, and Byzantine attack quarantine logs.

### 6.4. Enterprise SIEM Log Forwarding & Support Telemetry (Phase 34)
* **SIEM Exporter (`backend/app/infrastructure/logging/siem_exporter.py`)**:
  * Streams real-time audit events formatted according to **Syslog RFC 5424** and **Elastic Common Schema (ECS)**.
  * Native connectors for enterprise bank SIEM platforms: **Splunk HEC**, **Elasticsearch / Logstash**, and **Datadog**.
* **Support Diagnostic Bundler (`backend/app/application/services/support_diagnostics.py`)**:
  * Gathers redacted system diagnostic bundles (`cfi_support_bundle_*.tar.gz`) for support ticket triage via `SupportDiagnosticCompiler` (executable via `scripts/cfi_cli.py export-diagnostics`).
  * PII redaction engine strips account numbers, PANs, and sensitive credentials.
* **Verification**: `backend/tests/unit/test_siem_support_diagnostics.py` (8/8 unit tests pass).
* **Technical Guide**: `docs/siem_and_support_guide.md`.

---

## 7. Automated Infrastructure Verification & Test Matrix

All infrastructure components are continuously verified via automated tests executed in the platform CI pipeline:

| Component / Subsystem | Target Verification File | Criteria & Scope | Status | Test Command |
| :--- | :--- | :--- | :--- | :--- |
| **Multi-Cloud Terraform** | `test_terraform_templates.py` | AWS, Azure, GCP VPCs, subnets, EKS/AKS/GKE clusters, KMS, and security groups | ✅ PASSED | `python -m pytest backend/tests/unit/test_terraform_templates.py` |
| **Kubernetes Helm Packaging** | `test_helm_chart.py` | Chart SemVer, values schema, HPA, NetPol, PDB, and security posture | ✅ PASSED | `python -m pytest backend/tests/unit/test_helm_chart.py` |
| **K8s Dry-Run Renderer** | `validate_k8s_manifests.py` | Rendered template dry-run client validation against Kubernetes API schema | ✅ PASSED | `python scripts/validate_k8s_manifests.py` |
| **Production Docker Stack** | `verify_docker_deployment.py` | Master compose spec, Dockerfiles, Nginx security headers, WebSocket keepalive | ✅ PASSED | `python scripts/verify_docker_deployment.py` |
| **Docker Compose Manifests** | `test_docker_manifests.py` | Environment variable parity, secret generation, and gateway routing rules | ✅ PASSED | `python -m pytest backend/tests/unit/test_docker_manifests.py` |
| **OpenTelemetry Distributed Tracing** | `test_otel_tracer.py` | W3C traceparent headers, span lifecycle, 6-stage training spans, hardware metrics | ✅ PASSED | `python -m pytest backend/tests/unit/test_otel_tracer.py` |
| **Multi-Region Disaster Recovery** | `test_disaster_recovery_failover.py` | Coordinator heartbeat ping, 45s failure threshold, and automatic standby promotion | ✅ PASSED | `python -m pytest backend/tests/unit/test_disaster_recovery_failover.py` |
| **Perimeter WAF & Air-Gap** | `test_perimeter_airgap.py` | WAF rule inspection, air-gapped bundle completeness, and SHA-256 checksums | ✅ PASSED | `python -m pytest backend/tests/unit/test_perimeter_airgap.py` |
| **Enterprise SIEM Exporter** | `test_siem_support_diagnostics.py` | Syslog RFC 5424 / ECS log formatting and support diagnostic bundling | ✅ PASSED | `python -m pytest backend/tests/unit/test_siem_support_diagnostics.py` |
| **Backup & PITR Verifier** | `test_backup_verifier.py` | Point-in-time recovery dry-run and model checkpoint vault restore verification | ✅ PASSED | `python -m pytest backend/tests/unit/test_backup_verifier.py` |
| **Zero-Downtime Blue/Green** | `test_zero_downtime_deployment.py` | Argo Rollouts blue/green traffic shifting and client upgrade coordination | ✅ PASSED | `python -m pytest backend/tests/unit/test_zero_downtime_deployment.py` |
