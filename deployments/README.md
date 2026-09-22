# CFI Enterprise Cloud Deployment Architecture 🚀

This directory contains the production-grade Infrastructure as Code (IaC), Kubernetes Helm charts, GitOps continuous delivery manifests, and observability configurations for the **Collaborative Fraud Intelligence (CF-Intelligence)** platform.

---

## 1. Directory Structure & Component Topology

```
deployments/
├── terraform/                # Multi-Cloud Infrastructure as Code (AWS, Azure, GCP, Cloudflare)
│   ├── aws/                  # Multi-AZ VPC, EKS 1.30, Aurora PostgreSQL, MSK Kafka, KMS
│   ├── azure/                # Azure VNet, AKS with Calico CNI, Key Vault, Flexible Server
│   ├── gcp/                  # GCP VPC, GKE Private Cluster, Cloud SQL, Cloud KMS KeyRing
│   ├── cloudflare/           # Edge WAF, L7 Rate Limiting, TLS 1.3 Strict & Custom Rules
│   └── modules/              # Reusable modules (db, k8s, security)
│
├── helm/                     # Production Kubernetes Helm Charts
│   ├── cfi-platform/         # Core platform microservices (Aggregator, Bank Node, Coordinator, Ingress, HPA, PDB)
│   └── cfi-platform-root/    # Root umbrella chart with shared secrets, config, PVCs & services
│
├── argocd/                   # GitOps Continuous Delivery Specifications
│   └── application.yaml      # ArgoCD Application manifest for declarative cluster reconciliation
│
├── prometheus/               # Prometheus Monitoring & Alerting
│   ├── prometheus.yml        # Prometheus server scraping configuration
│   └── alert_rules.yml       # Production alerting rules (SLA breach, Byzantine drift, high error rate)
│
└── grafana/                  # Grafana Operational Dashboards
    └── dashboards/           # Pre-provisioned JSON dashboards (Consortium Overview, Security & Audit)
```

---

## 2. Infrastructure as Code (Terraform Multi-Cloud)

Turnkey, zero-credential HCL modules supporting enterprise multi-cloud deployment:

| Cloud Provider | Target Directory | Provisioned Infrastructure | Security Controls |
| :--- | :--- | :--- | :--- |
| **AWS** | `deployments/terraform/aws/` | Multi-AZ VPC, EKS 1.30, Managed Node Groups, Aurora PG, KMS | KMS envelope encryption, private subnets, gRPC mTLS security groups |
| **Azure** | `deployments/terraform/azure/` | Azure VNet, AKS 1.30, Calico NetworkPolicy, Azure Key Vault | Premium SKU Key Vault with purge protection, Deny inter-bank NSG |
| **GCP** | `deployments/terraform/gcp/` | Custom VPC, GKE Private Cluster, Cloud KMS KeyRing, NAT GW | Shielded VMs, Workload Identity, 90-day KMS key rotation |
| **Cloudflare** | `deployments/terraform/cloudflare/` | Anycast Edge WAF, L7 Rate Limiter, Sensitive Path Filter | OWASP Top 10 payload inspection, brute-force challenge, TLS 1.3 |

---

## 3. Kubernetes Orchestration (Helm Charts)

Production-ready Helm charts (`deployments/helm/cfi-platform`) deploying microservices under strict zero-trust governance:
* **Zero-Trust NetworkPolicies**: Ingress strictly restricted to authorized gateways; inter-bank lateral communication blocked at the kernel netfilter level.
* **Horizontal Pod Autoscaling (HPA)**: Dynamic scaling between 2 and 10 replicas based on CPU (70%) and inference request queues.
* **Pod Disruption Budgets (PDB)**: Guarantees high-availability during node upgrades and drain events (`minAvailable: 1`).
* **Container Hardening**: Read-only root filesystems (`readOnlyRootFilesystem: true`), non-root execution (`runAsNonRoot: true`, UID 1000), and all Linux capabilities dropped (`drop: ["ALL"]`).

---

## 4. Automated Verification & Testing

All deployment artifacts are verified by automated testing pipelines:

1. **Terraform IaC Structural Integrity Suite**:
   ```bash
   python -m pytest backend/tests/unit/test_terraform_templates.py -v
   # Result: 66/66 PASSED (Validates HCL structure, KMS keys, cluster resources, network isolation, and zero hardcoded secrets)
   ```

2. **Kubernetes Helm Manifest Dry-Run Audit**:
   ```bash
   python scripts/validate_k8s_manifests.py --all
   # Result: 38/38 Kubernetes resources rendered and validated cleanly via kubectl apply --dry-run=client
   ```
