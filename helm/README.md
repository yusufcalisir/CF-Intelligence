# Privacy-Preserving Cross-Bank Fraud Detection (CFI) — Helm Charts

This directory contains the standalone, unified production Helm chart for deploying the **Privacy-Preserving Cross-Bank Fraud Detection Platform (CFI)** to Kubernetes clusters.

---

## 1. Directory Structure

```text
helm/
├── README.md                          # Helm architecture, configuration reference & validation guide
└── cfi-platform/                      # Unified Helm 3 application chart
    ├── Chart.yaml                     # Chart metadata (Version: 1.0.0, AppVersion: 1.4.2)
    ├── values.yaml                    # Default values (replicas, security contexts, ingress, autoscaling)
    └── templates/
        ├── deployment.yaml            # Backend API & FL engine deployment (ports 8000, 50051)
        ├── service.yaml               # ClusterIP service routing HTTP & gRPC traffic
        ├── hpa.yaml                   # HorizontalPodAutoscaler (2-20 replicas @ 70% CPU)
        └── ingress.yaml               # Ingress resource with TLS termination & rate limiting
```

---

## 2. Resource Specifications

The chart renders and deploys **4 validated Kubernetes resources**:

| Resource | Kind | Name | Port / Config |
|:---|:---|:---|:---|
| **Deployment** | `apps/v1` | `cfi-platform-deployment` | Ports `8000` (HTTP), `50051` (gRPC), non-root UID `10001` |
| **Service** | `v1` | `cfi-platform-service` | `ClusterIP` exposing `8000` (HTTP) and `50051` (gRPC) |
| **HPA** | `autoscaling/v2` | `cfi-platform-hpa` | Min `2`, Max `20`, Target CPU utilization `70%` |
| **Ingress** | `networking.k8s.io/v1` | `cfi-platform-ingress` | Nginx Ingress with cert-manager Let's Encrypt TLS |

---

## 3. Configuration Reference (`values.yaml`)

| Parameter | Type | Default | Description |
|:---|:---|:---|:---|
| `replicaCount` | `int` | `2` | Initial replica count |
| `image.repository` | `string` | `cfi-intelligence/backend` | Container image repository |
| `image.tag` | `string` | `latest` | Container image tag |
| `image.pullPolicy` | `string` | `IfNotPresent` | Image pull policy |
| `serviceAccount.create` | `bool` | `true` | Create dedicated Kubernetes ServiceAccount |
| `podSecurityContext.runAsNonRoot` | `bool` | `true` | Enforce non-root execution |
| `podSecurityContext.runAsUser` | `int` | `10001` | Non-root runtime user UID |
| `service.type` | `string` | `ClusterIP` | Kubernetes Service type |
| `service.port` | `int` | `8000` | HTTP API port |
| `service.grpcPort` | `int` | `50051` | gRPC Mutual TLS port |
| `ingress.enabled` | `bool` | `true` | Enable Kubernetes Ingress resource |
| `ingress.className` | `string` | `nginx` | Ingress controller class |
| `resources.limits.cpu` | `string` | `2000m` | CPU limit |
| `resources.limits.memory` | `string` | `4Gi` | Memory limit |
| `autoscaling.enabled` | `bool` | `true` | Enable HorizontalPodAutoscaler |
| `autoscaling.minReplicas` | `int` | `2` | Minimum autoscaling replicas |
| `autoscaling.maxReplicas` | `int` | `20` | Maximum autoscaling replicas |

---

## 4. Helm Charts Topology

The repository provides two distinct Helm architectures:

1. **`helm/cfi-platform` (This Directory)**:
   - Standalone, unified chart packaging the core platform into a streamlined single-release model.
   - Ideal for staging, single-node evaluation, or simplified enterprise on-premises deployments.
2. **`deployments/helm/` (Enterprise GitOps Mesh)**:
   - `deployments/helm/cfi-platform`: Decomposed microservices chart (16 resources) featuring isolated aggregator, bank nodes, NetworkPolicies, and PodDisruptionBudgets.
   - `deployments/helm/cfi-platform-root`: ArgoCD GitOps umbrella chart (19 resources) with ConfigMaps, PVCs, and secret management.

---

## 5. Deployment & Quick Start

```bash
# 1. Lint the chart
helm lint helm/cfi-platform

# 2. Dry-run render
helm template cfi-release helm/cfi-platform

# 3. Install or upgrade release
helm upgrade --install cfi-release helm/cfi-platform \
  --namespace cfi-consortium \
  --create-namespace \
  -f helm/cfi-platform/values.yaml
```

---

## 6. Automated Dry-Run Verification

The chart is continuously validated against the Kubernetes API schema using `kubectl apply --dry-run=client`:

```bash
# Validate standalone chart
python scripts/validate_k8s_manifests.py --chart helm/cfi-platform

# Validate all repository charts (39 total Kubernetes resources)
python scripts/validate_k8s_manifests.py --all
```
