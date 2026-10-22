# Privacy-Preserving Cross-Bank Fraud Detection (CFI) — Helm Charts

This directory contains the standalone, unified production Helm chart for deploying the **Privacy-Preserving Cross-Bank Fraud Detection Platform (CFI)** to Kubernetes clusters.

---

## 1. Directory Structure

```text
helm/
├── README.md                          # Helm architecture, configuration reference & validation guide
└── cfi-platform/                      # Unified Helm 3 application chart
    ├── Chart.yaml                     # Chart metadata (Version: 1.0.0, AppVersion: 2.4.0)
    ├── values.yaml                    # Default values (replicas, security contexts, probes, ingress, autoscaling)
    └── templates/
        ├── deployment.yaml            # Backend API & FL engine deployment (ports 8000, 50051)
        ├── service.yaml               # ClusterIP service routing HTTP & gRPC traffic
        ├── hpa.yaml                   # HorizontalPodAutoscaler (1-5 replicas @ 70% CPU)
        ├── ingress.yaml               # Ingress resource with TLS termination & rate limiting
        └── pvc.yaml                   # PersistentVolumeClaim for /app/storage (ReadWriteOnce)
```

---

## 2. Resource Specifications

The chart renders and deploys **5 validated Kubernetes resources**:

| Resource | Kind | Name | Port / Config |
|:---|:---|:---|:---|
| **Deployment** | `apps/v1` | `cfi-platform-deployment` | Ports `8000` (HTTP), `50051` (gRPC), non-root UID `1000`, `/health` liveness & `/ready` readiness probes |
| **Service** | `v1` | `cfi-platform-service` | `ClusterIP` exposing `8000` (HTTP) and `50051` (gRPC) |
| **PVC** | `v1` | `cfi-platform-storage-pvc` | Persistent volume claim for `/app/storage` (10Gi, `ReadWriteOnce`) |
| **HPA** | `autoscaling/v2` | `cfi-platform-hpa` | Min `1`, Max `5`, Target CPU utilization `70%` (optional/disabled by default) |
| **Ingress** | `networking.k8s.io/v1` | `cfi-platform-ingress` | Nginx Ingress with cert-manager Let's Encrypt TLS |

---

## 3. Configuration Reference (`values.yaml`)

| Parameter | Type | Default | Description |
|:---|:---|:---|:---|
| `replicaCount` | `int` | `1` | Initial replica count (guardrail enforced for ReadWriteOnce storage) |
| `image.repository` | `string` | `cfi-intelligence/backend` | Container image repository |
| `image.tag` | `string` | `latest` | Container image tag |
| `image.pullPolicy` | `string` | `IfNotPresent` | Image pull policy |
| `serviceAccount.create` | `bool` | `true` | Create dedicated Kubernetes ServiceAccount |
| `podSecurityContext.runAsNonRoot` | `bool` | `true` | Enforce non-root execution |
| `podSecurityContext.runAsUser` | `int` | `1000` | Non-root runtime user UID |
| `podSecurityContext.fsGroup` | `int` | `1000` | Non-root runtime filesystem group GID |
| `securityContext.allowPrivilegeEscalation` | `bool` | `false` | Prevent container privilege escalation |
| `persistence.enabled` | `bool` | `true` | Enable persistent storage for registry & ledgers |
| `persistence.size` | `string` | `10Gi` | Persistent storage allocation |
| `persistence.mountPath` | `string` | `/app/storage` | Container storage mount target |
| `existingSecret` | `string` | `cfi-platform-secrets` | Pre-existing Kubernetes Secret name |
| `service.type` | `string` | `ClusterIP` | Kubernetes Service type |
| `service.port` | `int` | `8000` | HTTP API port |
| `service.grpcPort` | `int` | `50051` | gRPC Mutual TLS port |
| `ingress.enabled` | `bool` | `true` | Enable Kubernetes Ingress resource |
| `ingress.className` | `string` | `nginx` | Ingress controller class |
| `resources.limits.cpu` | `string` | `2000m` | CPU limit |
| `resources.limits.memory` | `string` | `4Gi` | Memory limit |
| `autoscaling.enabled` | `bool` | `false` | Enable HorizontalPodAutoscaler |
| `autoscaling.minReplicas` | `int` | `1` | Minimum autoscaling replicas |
| `autoscaling.maxReplicas` | `int` | `5` | Maximum autoscaling replicas |
| `autoscaling.targetCPUUtilizationPercentage` | `int` | `70` | Target CPU utilization percentage for autoscaling |
| `probes.liveness.path` | `string` | `/health` | Kubernetes container liveness probe endpoint path |
| `probes.liveness.initialDelaySeconds` | `int` | `15` | Liveness probe initial delay in seconds |
| `probes.liveness.periodSeconds` | `int` | `10` | Liveness probe check period in seconds |
| `probes.readiness.path` | `string` | `/ready` | Kubernetes container readiness probe endpoint path |
| `probes.readiness.initialDelaySeconds` | `int` | `5` | Readiness probe initial delay in seconds |
| `probes.readiness.periodSeconds` | `int` | `5` | Readiness probe check period in seconds |
| `env.APP_ENV` | `string` | `production` | Application deployment environment identifier |
| `env.REDIS_URL` | `string` | `redis://cfi-redis:6379/0` | Upstream Redis cluster connection URI |
| `env.TELEMETRY_ENABLED` | `string` | `true` | OpenTelemetry and Prometheus metric collection flag |

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
