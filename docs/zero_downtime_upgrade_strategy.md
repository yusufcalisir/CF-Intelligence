# 🔄 Zero-Downtime Platform Upgrade & Client Compatibility Strategy

The Collaborative Fraud Intelligence (CFI) platform utilizes the **Zero-Downtime Deployment Manager** (`ZeroDowntimeDeploymentManager`) to orchestrate rolling cluster upgrades, graceful client connection draining, and dual-version compatibility windows, eliminating downtime during enterprise releases.

---

## 📌 Architectural Upgrade Pipeline

```
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                        ZERO-DOWNTIME ROLLING UPGRADE LIFECYCLE                         │
│                                                                                        │
│   [ Steady State: Current Version v2.0.0 ]                                             │
│                     │                                                                  │
│                     ▼  initiate_upgrade(target_version="v2.1.0", window=48h)           │
│   ┌──────────────────────────────────────────────────────────┐                         │
│   │ 1. DRAINING_CONNECTIONS                                  │                         │
│   │    - Intercept new connection handshakes                 │                         │
│   │    - Batch-drain active gRPC / WebSocket streams         │                         │
│   │    - Route new traffic to standby ingress                │                         │
│   └──────────────────────────────────────────────────────────┘                         │
│                     │ (Active Connections == 0)                                        │
│                     ▼                                                                  │
│   ┌──────────────────────────────────────────────────────────┐                         │
│   │ 2. ROLLING_UPGRADE                                       │                         │
│   │    - Batch pod updates (MaxSurge=25%, MaxUnavailable=0)  │                         │
│   │    - Execute forward-compatible Alembic migrations       │                         │
│   └──────────────────────────────────────────────────────────┘                         │
│                     │ execute_rolling_instance_update                                  │
│                     ▼                                                                  │
│   ┌──────────────────────────────────────────────────────────┐                         │
│   │ 3. DUAL_VERSION_ACTIVE (48-Hour Compatibility Window)    │                         │
│   │    - Both v2.0.0 and v2.1.0 endpoints coexist            │                         │
│   │    - Headers: x-cfi-deprecation-warning                  │                         │
│   │    - Bank node SDKs execute non-disruptive migration     │                         │
│   └──────────────────────────────────────────────────────────┘                         │
│                     │ finalize_upgrade                                                 │
│                     ▼                                                                  │
│   [ 4. UPGRADE_COMPLETED: Current Version Promoted to v2.1.0 ]                         │
└────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## ⚙️ Core Deployment States (`app.domain.deployment_state`)

```python
class DeploymentStage(str, Enum):
    IDLE = "IDLE"
    DRAINING_CONNECTIONS = "DRAINING_CONNECTIONS"
    ROLLING_UPGRADE = "ROLLING_UPGRADE"
    DUAL_VERSION_ACTIVE = "DUAL_VERSION_ACTIVE"
    UPGRADE_COMPLETED = "UPGRADE_COMPLETED"
```

| Deployment Stage | Cluster Actions | Client Ingress Experience |
| :--- | :--- | :--- |
| **`DRAINING_CONNECTIONS`** | Active instances finish inflight transactions. gRPC streams and WebSockets are notified to reconnect to the green tier. | Zero request drops; existing requests complete up to timeout. |
| **`ROLLING_UPGRADE`** | New target version pods are provisioned and health-probed (`/health` + `/readyz`). | Inbound requests are serviced by the newly provisioned instances. |
| **`DUAL_VERSION_ACTIVE`** | Both API schemas are served simultaneously via version-routed gateway adapters. | Legacy clients receive deprecation headers while maintaining 100% SLA. |
| **`UPGRADE_COMPLETED`** | Legacy pods are terminated; `current_version` is atomically promoted. | All traffic transitions to the target version. |

---

## 🔌 Connection Draining Mechanics (`drain_client_connections`)

Connection draining prevents connection abrupt drops during pod evictions:
```python
manager = ZeroDowntimeDeploymentManager(current_version="v2.0.0")
session = manager.initiate_upgrade(
    target_version="v2.1.0",
    compatibility_window_hours=48,
    initial_connections=100
)

# Drain in controlled batches of 50 connections
active, drained = manager.drain_client_connections(session.session_id, batch_size=50)
assert active == 50
assert drained == 50

# Second drain exhausts remaining pool -> Stage advances to ROLLING_UPGRADE
active, drained = manager.drain_client_connections(session.session_id, batch_size=50)
assert active == 0
assert session.stage == DeploymentStage.ROLLING_UPGRADE
```

---

## 🗄️ Database Backward Compatibility Invariants

To avoid locking or breaking active transactions during the `DUAL_VERSION_ACTIVE` stage:
1. **Additive Schema Changes Only**: New columns must be added as `NULLABLE` or carry deterministic `DEFAULT` clauses (as enforced in Alembic migration `002_core_and_aml_tables.py`).
2. **Never Drop Columns In-Flight**: Dropping or renaming columns requires a two-release deprecation cycle:
   - *Release N*: Add new column and dual-write in application code.
   - *Release N+1*: Migrate readers to new column.
   - *Release N+2*: Drop legacy column in Alembic migration.
3. **SQLite Batch Mode Safety**: In SQLite standalone mode, alterations utilize `render_as_batch=True` to reconstruct tables in temporary tables with zero file corruption.

---

## 🧪 Automated Test Suite Validation

```bash
pytest backend/tests/unit/test_zero_downtime_deployment.py -v
```

**Verification Results:**
- `test_zero_downtime_upgrade_initiation_and_connection_draining`: `PASSED`
- `test_rolling_instance_update_and_finalization`: `PASSED` (Verifies version promotion to target release)
