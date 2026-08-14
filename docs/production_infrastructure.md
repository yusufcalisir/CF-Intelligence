# Enterprise Cloud Production Infrastructure & Live Orchestration Blueprint (2026 Edition)

---

## 1. Executive Overview: From Docker-Compose to Multi-Region Cloud Orchestration

While local developers use `docker-compose.yml` for offline testing, **production deployments of CF-Intelligence operate on a multi-region, auto-scaling, Kubernetes (EKS/GKE) and managed infrastructure mesh**.

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
            │ • EKS Cluster (HPA 3-30 Pods)             │       │ • EKS Cluster (Min 2 Pods Standby)        │
            │ • Ingress-NGINX + mTLS 1.3 Strict         │       │ • Ingress-NGINX + mTLS 1.3 Strict         │
            │ • Strimzi Kafka (pacs.008 stream)         │ ───►  │ • MirrorMaker 2 Kafka Cross-Region Sync   │
            │ • AWS Aurora Multi-AZ PostgreSQL (RLS)    │ ───►  │ • Aurora Global DB Read Replica (Sync)   │
            │ • Redis HA Sentinel (Tenant Namespaces)   │ ───►  │ • Redis Standby Cluster Replication       │
            │ • Prometheus / OTel / Grafana Dashboards  │       │ • Prometheus Standby Scraper              │
            └───────────────────────────────────────────┘       └───────────────────────────────────────────┘
```

---

## 2. Infrastructure as Code (IaC) & Cloud Provisioning Stack

### 2.1. Terraform Multi-Region Blueprint (`deployments/terraform/aws/`)
* **VPC & Subnet Topology**: Dual-region multi-AZ deployment (`eu-central-1a/b/c` and `eu-west-1a/b/c`) with private-isolated subnets for database and TEE enclaves.
* **EKS Managed Node Groups**: Kubernetes 1.30 with GPU acceleration (`g5.2xlarge` with NVIDIA A10G for local GNN embedding training) and confidential computing instances (`m6i.metal` for Intel SGX v2).
* **Managed Data Planes**:
  * **AWS MSK / Strimzi Kafka Operator**: High-throughput distributed partition streaming for ISO 20022 message ingestion (`pacs.008`, `camt.053`) with TLS client authentication.
  * **AWS Aurora Multi-AZ PostgreSQL**: Multi-tenant database engine with Row-Level Security (RLS) policies and read replicas.
  * **AWS ElastiCache Redis Cluster**: Redis Sentinel HA with TLS encryption in transit and per-tenant key namespacing (`cfi:tenant:<bank_id>:*`).

---

### 2.2. Kubernetes Helm Packaging & Horizontal Pod Autoscaling (`HPA`)
* **Helm Chart**: `deployments/helm/cfi-platform/`
* **Horizontal Pod Autoscaler (HPA)**:
  * Scales `cfi-scoring-engine` pods dynamically based on CPU utilization ($>70\%$) and active request rate ($>250\text{ req/sec/pod}$ via KEDA Prometheus metrics).
  * Scale range: Minimum $3$ pods, maximum $30$ pods per region.
* **Network Policies**: Strict zero-trust inter-pod communication; coordinator pods cannot access tenant database ports directly.

---

## 3. End-to-End Live Observability & Telemetry Architecture

1. **OpenTelemetry (OTel) Distributed Tracing**:
   Every credit transfer scoring request is injected with a `traceparent` header (W3C standard), tracing spans across API Gateway ($1.2\text{ms}$) $\to$ GNN Embedding Inference ($8.4\text{ms}$) $\to$ XGBoost Calibration ($3.6\text{ms}$) $\to$ Response ($<14.2\text{ms}$ total).
2. **Prometheus Metrics Pipeline**:
   Exposes real-time Prometheus endpoints on `:9090/metrics`:
   * `cfi_scoring_latency_seconds_bucket` (p50, p95, p99 histograms).
   * `cfi_fl_round_gradient_norm` and `cfi_dp_epsilon_consumed_total`.
   * `cfi_active_bank_nodes_gauge` and `cfi_byzantine_rejections_total`.
3. **Grafana Production Dashboards**:
   Pre-configured visual dashboards in `deployments/grafana/dashboards/`:
   * *Executive Overview Dashboard*: Real-time global fraud detection rate and cost savings.
   * *Security & Privacy Dashboard*: Cumulative $(\varepsilon, \delta)$ budget consumption and mTLS handshake telemetry.
   * *Cluster Health & DR Dashboard*: Multi-region coordinator status and Raft replication lag.
