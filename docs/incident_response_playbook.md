# Enterprise 24/7 On-Call Incident Response Runbooks & SRE Playbooks (P0-P4)

**Document Reference:** `CFI-OPS-INCIDENT-2026-V2`  
**Integration Anchors:** PagerDuty / Opsgenie / Slack War Rooms / HashiCorp Vault PKI / Prometheus Alertmanager.

---

## 1. Incident Severity Classification & On-Call Response Matrix

```
┌────────────────────────────────────────────────────────────────────────────────────────────────────────┐
│                        24/7 ON-CALL INCIDENT SEVERITY & ESCALATION MATRIX                              │
├──────────────┬──────────────────────────────────────────┬──────────────┬───────────────┬───────────────┤
│ SEVERITY     │ DEFINITION & TRIGGER CONDITION           │ ON-CALL SLA  │ WAR ROOM      │ RESOLUTION SLA│
├──────────────┼──────────────────────────────────────────┼──────────────┼───────────────┼───────────────┤
│ **P0: BLOCK**│ Multi-region failure or privacy leak     │ ≤ 5 minutes  │ Exec Bridge   │ ≤ 1 hour      │
│ **P1: CRIT** │ Latency >50ms or Byzantine attack        │ ≤ 15 minutes │ SRE War Room  │ ≤ 2 hours     │
│ **P2: MAJOR**│ Concept drift alert (PSI > 0.25)         │ ≤ 1 hour     │ Eng Slack     │ ≤ 6 hours     │
│ **P3: MOD**  │ Single bank node straggler timeout       │ ≤ 4 hours    │ Support Ticket│ ≤ 24 hours    │
│ **P4: MINOR**│ Non-critical metric scraping anomaly     │ Next Bus. Day│ Standard Queue│ Next Release  │
└──────────────┴──────────────────────────────────────────┴──────────────┴───────────────┴───────────────┘
```

---

## 2. Operational Incident Response Runbooks

### 2.1. RUNBOOK-P0-01: Multi-Region Regional Coordinator Outage
* **Trigger**: Prometheus alert `CoordinatorHeartbeatMissing > 15s` in Primary Region (`eu-central-1`).
* **Automated Action**: `MultiRegionFailoverManager` executes automated promotion of Standby Region (`eu-west-1`) (Measured RTO: $15.02\text{s} \le 30.0\text{s}$, RPO: $0$).
* **On-Call SRE Execution Steps**:
  1. Join `#incident-p0-outage` war room.
  2. Verify Route53 DNS switch: `dig +short api.cf-intelligence.bank`.
  3. Validate database synchronization lag: `SELECT EXTRACT(EPOCH FROM (now() - last_replay_time)) FROM pg_stat_replication;` (Must be $< 1.0\text{s}$).
  4. Post public status page incident notice within 15 minutes.

---

### 2.2. RUNBOOK-P1-01: Real-Time Scoring Latency Degradation (p99 > 50ms)
* **Trigger**: `cfi_scoring_latency_seconds{quantile="0.99"} > 0.050` for $> 60\text{s}$.
* **On-Call SRE Execution Steps**:
  1. Inspect Kubernetes HPA autoscaling status: `kubectl get hpa cfi-scoring-engine -n cfi-prod`.
  2. Force emergency horizontal scale-out: `kubectl scale deployment cfi-scoring-engine --replicas=20 -n cfi-prod`.
  3. Check Redis cache hit ratio: `redis-cli -h cfi-cache info stats | grep keyspace_hits`.
  4. If inference bottleneck is GNN subgraph hop sampling, temporarily degrade hop depth from $k=3$ to $k=2$ via dynamic configuration flag:
     `curl -X POST https://api.cfi.internal/v1/admin/config -d '{"gnn_hop_depth": 2}'`.

---

### 2.3. RUNBOOK-P1-02: Byzantine Gradient Poisoning & Compromised Node Revocation
* **Trigger**: `cfi_byzantine_rejections_total > 5` within a single federated training round.
* **On-Call SRE Execution Steps**:
  1. Identify offending bank node ID from Krum cosine outlier logs:
     `kubectl logs -l app=cfi-coordinator -n cfi-prod | grep "BYZANTINE_ANOMALY"`.
  2. Isolate offending node via network policy:
     `kubectl label node bank-gamma-agent cfi.network/quarantine=true`.
  3. Revoke Vault PKI mTLS client certificate:
     `vault write pki/revoke serial_number=<GAMMA_CERT_SERIAL>`.
  4. Initiate **First-Order Hessian Inversion Unlearning** to erase compromised gradient contributions from latest checkpoint.

---

### 2.4. RUNBOOK-P2-01: Severe Concept Drift Alert ($PSI \ge 0.25$)
* **Trigger**: `ModelDriftService` reports `overall_psi >= 0.25` or `ks_p_value < 0.01`.
* **On-Call ML Engineer Execution Steps**:
  1. Inspect feature drift distributions in Grafana Model Governance dashboard.
  2. Trigger expedited emergency Federated Learning round:
     `python -m app.application.services.automated_retraining --urgency=HIGH --rounds=10`.
  3. Evaluate candidate model fairness in staging sandbox:
     `pytest backend/tests/unit/test_sr11_7_model_governance.py -k test_disparate_impact_fairness`.
  4. Execute atomic canary deployment to participating bank edge nodes.
