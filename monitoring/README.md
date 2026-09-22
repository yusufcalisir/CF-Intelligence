# Enterprise Monitoring, Telemetry & Observability Mesh

This directory contains the production-grade observability stack for the **Privacy-Preserving Cross-Bank Fraud Detection Platform (CFI)**, integrating **Prometheus** (metrics aggregation), **Grafana** (real-time dashboards), **Alertmanager** (incident routing), **Loki** & **Promtail** (structured log shipping), and **Jaeger** (distributed tracing).

---

## 1. Directory Structure

```text
monitoring/
├── README.md                          # Telemetry architecture, metric dictionaries & SLA alerts
├── prometheus.yml                     # Prometheus server configuration & scrape targets
├── jaeger-ui-config.json              # Jaeger distributed tracing UI configuration
├── alertmanager/
│   └── config.yml                     # Incident routing, alert grouping & webhook notifications
├── prometheus/
│   └── alert_rules.yml                # Prometheus SLA alert definitions (Latency, Quorum, DP, Drift)
├── grafana/
│   ├── provisioning/
│   │   ├── datasources/
│   │   │   └── datasources.yml        # Auto-provisioned Prometheus & Jaeger datasources
│   │   └── dashboards/
│   │       └── dashboards.yml         # Dashboard provider registration
│   └── dashboards/
│       ├── cfi-overview.json          # High-level HTTP throughput, latency p95 & error rates
│       ├── cfi_operational_dashboard.json # Active bank nodes, scoring throughput & latency p50/p95/p99
│       └── cfi_privacy_budget_dashboard.json # DP epsilon consumption, budget projection & exhaustion
├── loki/
│   └── loki-config.yml                # Grafana Loki log aggregation engine configuration
└── promtail/
    └── promtail-config.yml            # Promtail container log shipping agent
```

---

## 2. Core Observability Components

### 2.1 Prometheus Metrics Scraper (`prometheus.yml`)
- **Scrape Interval**: 15s (`evaluation_interval: 15s`).
- **Scrape Targets**:
  - `cfi-gateway` (`gateway:8000/metrics/`): Reverse proxy and public REST endpoints.
  - `cfi-fl-coordinator` (`fl-coordinator:8001/metrics/`): Federated Learning round progression and consensus.
  - `cfi-identity-graph` (`identity-graph:8002/metrics/`): Inductive GraphSAGE embeddings & UBO topology.
  - `cfi-fraud-alert` (`fraud-alert:8003/metrics/`): Real-time inference scoring, AML rules, and sanction screening.
  - `prometheus` (`localhost:9090`): Self-monitoring.

### 2.2 Alert Rules & SLA Invariants (`prometheus/alert_rules.yml`)

The platform enforces four automated SLA alerts:

| Alert Name | Severity | Condition / PromQL | Description |
|:---|:---|:---|:---|
| **`CFI_HighInferenceLatency`** | `critical` | `histogram_quantile(0.95, sum(rate(cfi_inference_latency_ms_bucket[5m])) by (le)) > 100` | Real-time transaction scoring p95 latency exceeds 100ms for > 2m. |
| **`CFI_QuorumRisk`** | `warning` | `cfi_active_bank_nodes < 2` | Active participating bank nodes drop below minimum consortium quorum (2 nodes) for > 5m. |
| **`CFI_DPBudgetDepleting`** | `warning` | `cfi_dp_epsilon_consumed_total > 6.4` | Differential privacy epsilon consumption exceeds 80% (6.4 / 8.0) of maximum budget. |
| **`CFI_ModelDrift`** | `critical` | `cfi_champion_model_auc < 0.75` | Champion global model holdout evaluation AUC drops below critical threshold 0.75. |

### 2.3 Alertmanager Incident Routing (`alertmanager/config.yml`)
- Groups alerts by `alertname` and `severity`.
- Emits webhooks to `http://gateway:8000/api/v1/monitoring/alerts/webhook` with `repeat_interval: 1h`.

### 2.4 Grafana Dashboards (`grafana/dashboards/`)
Auto-loaded upon container boot via Grafana provisioning:
1. **`cfi-overview.json`**:
   - HTTP Request Rate (`reqps`)
   - Latency p95 (`ms`)
   - Active In-Flight Requests
2. **`cfi_operational_dashboard.json`**:
   - Active Consortium Bank Nodes (Gauge: Red `<2`, Yellow `2`, Green `3+`)
   - Real-Time Inference Latency (p50, p95, p99 timeseries)
   - Transaction Scoring Volume Rate (Barchart)
3. **`cfi_privacy_budget_dashboard.json`**:
   - DP Epsilon Consumed per Bank Node (Stacked barchart)
   - Time to Privacy Budget Exhaustion Projection (Hours to exhaustion)
   - Remaining Privacy Budget Percentage (Gauge)

### 2.5 Centralized Logging & Tracing
- **Loki (`loki/loki-config.yml`)**: Single-tenant TSDB engine storing compressed log chunks with 24h retention indices.
- **Promtail (`promtail/promtail-config.yml`)**: Scrapes container logs from `/var/log/containers/*log` and streams to Loki on port 3100.
- **Jaeger (`jaeger-ui-config.json`)**: Distributed trace viewer inspecting OpenTelemetry spans (`cfi_grpc_request_duration_seconds`, etc.).

---

## 3. Automated Verification

The monitoring configurations and dashboard schemas are continuously validated in CI:

```bash
# Run telemetry & dashboard schema verification tests
python -m pytest backend/tests/unit/test_telemetry_metrics.py -v
```
