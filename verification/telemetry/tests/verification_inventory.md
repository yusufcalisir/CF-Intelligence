# Scientific Verification Inventory — Telemetry & Observability Subsystem

**Project:** Privacy-Preserving Cross-Bank Fraud Detection using Federated Learning  
**Subsystem:** Telemetry, Metrics, Distributed Tracing, SIEM Logging & SLA Monitoring  
**Audited Modules:**  
- `app/infrastructure/telemetry/__init__.py` (`TelemetryRegistry`, Prometheus Exporter, MetricProxy, Decorators)  
- `app/infrastructure/telemetry/otel_tracer.py` (`OpenTelemetryTracer`, W3C Context Propagation Engine)  
- `app/infrastructure/logging/siem_exporter.py` (`SIEMLogExporter`, Syslog RFC 5424, CEF, Splunk HEC, Datadog Intake, Retry Queue)  
- `app/application/services/sla_monitor.py` (`RealtimeSLAMonitor`, Quantile Interpolation Engine)  
- `app/application/services/tenant_metering.py` (`TenantMeteringService`, Resource Quota & Billing Estimator)  
- `app/application/services/alert_service.py` (`AlertIntelligenceService`, Risk Scaling & Anonymized Intelligence)  
- `app/application/services/support_diagnostics.py` (`SupportDiagnosticCompiler`, PII Redaction & SHA-256 Manifest)  
- `app/application/services/metrics_service.py` (`MetricsService`, Aggregate Model Improvement Evaluator)  
- `app/presentation/routers/monitoring.py` (Drift Analysis, Calibration & Prometheus Endpoint)  
- `app/presentation/routers/health.py` (Liveness `/health` & Readiness `/health/ready` Probes)  
- `app/presentation/routers/dashboard.py` (Investigation Dashboard Metrics & Risk Weights API)  

**Auditor Role:** Senior Researcher in Observability, Telemetry Systems, Distributed Systems, and Scientific Software Verification  
**Evaluation Standard:** High-Precision Observability Verification & Scientific Telemetry Audit  
**Date:** 2026-08-01  

---

## 1. Executive Summary

This inventory documents every telemetry collector, metrics aggregator, distributed tracing pipeline, security log exporter, SLA percentile engine, tenant quota manager, and diagnostic compiler implemented across the platform.

The telemetry subsystem combines **OpenTelemetry W3C context propagation**, **Prometheus pull-based metrics exposition**, **SIEM multi-format audit log forwarding (RFC 5424 / CEF / Splunk / Datadog)**, and **real-time SLA quantile tracking**.

Across all audited modules, **24 distinct telemetry mechanisms** were identified, mathematically formulated, and evaluated for implementation risks, edge cases, scientific validity, and appropriate verification procedures.

---

## 2. Telemetry Architecture Overview

```
┌──────────────────────────────────────────────────────────────────────────────────────────────────┐
│                               TELEMETRY & OBSERVABILITY ARCHITECTURE                            │
├──────────────────────────────────────────────────────────────────────────────────────────────────┤
│                                                                                                  │
│   FastAPI API / gRPC Servicer / Client Daemons                                                   │
│      │                                                                                           │
│      ├─── Distributed Tracing ────────────────────────► OpenTelemetryTracer                      │
│      │    (W3C traceparent / tracestate propagation)   ├─ Ingest / Feature / Local Trainer Spans │
│      │                                                 └─ gRPC Transmit & Central Agg Spans      │
│      │                                                                                           │
│      ├─── Metric Exposition ───────────► TelemetryRegistry & MetricProxy                         │
│      │    (Prometheus text exposition format)   ├─ Histograms (Latency, Round Durations)       │
│      │                                          ├─ Counters (DP Epsilon, Rejections, Anomalies)  │
│      │                                          └─ Gauges (Active Nodes, Champion AUC, Heartbeat)│
│      │                                                                                           │
│      ├─── Security Audit Logging ──────► SIEMLogExporter                                         │
│      │    (Multi-target SIEM forwarding)       ├─ Syslog RFC 5424 (UDP 514 / TCP 6514)            │
│      │                                         ├─ CEF Formatting & Splunk HEC / Datadog HTTP     │
│      │                                         └─ Offline Retry Queue (JSONL Buffer + Daemon)    │
│      │                                                                                           │
│      ├─── SLA Percentile Engine ───────► RealtimeSLAMonitor                                      │
│      │    (p50, p95, p99 Quantile Interpolation) └─ Linear Interpolation Quantiles & SLA %       │
│      │                                                                                           │
│      ├─── Tenant Quota & Billing ──────► TenantMeteringService                                   │
│      │    (Daily/Monthly Usage Counters)       └─ UTC Reset, Boundary Checks, Cost Calculation   │
│      │                                                                                           │
│      └─── Diagnostic Bundler ──────────► SupportDiagnosticCompiler                               │
│           (Sanitization & Cryptographic Proof) └─ PII Regex Stripping (IBAN/Email) & SHA-256   │
│                                                                                                  │
└──────────────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 3. Comprehensive Verification Inventory

---

### Component 1: `TelemetryRegistry` — Prometheus Metrics Exposition Engine
- **Module:** `app/infrastructure/telemetry/__init__.py`
- **Purpose:** Thread-safe Prometheus metrics registry for tracking counters, gauges, and histograms, rendering standard UTF-8 Prometheus text format responses (`GET /metrics`).
- **Mathematical / Statistical Formulation:**
  - Cumulative Counter: $C_{t} = C_{t-1} + \Delta x, \quad \Delta x \ge 0$
  - Histogram Sum: $S = \sum_{i=1}^{N} x_i$
  - Bucket Count: $B(b) = \sum_{i=1}^{N} \mathbb{I}(x_i \le b)$ for buckets $b \in \{10.0, 30.0, 50.0, 100.0, 200.0, 500.0\}$
- **Monitoring Claim:** Accurately aggregates latency histograms, active bank node counts, DP epsilon usage, and gradient rejections in standard Prometheus 0.0.4 text format.
- **Expected Invariant:**
  - $C_t \ge C_{t-1}$ (Counters are monotonically non-decreasing).
  - $B(b_1) \le B(b_2)$ for $b_1 \le b_2$ (Bucket counts are non-decreasing over threshold bounds).
  - $B(+\infty) = N = \text{Histogram Count}$.
- **Possible Implementation Risks:**
  - Unbounded list storage in `self._histograms` and `self._histogram_labels` leads to unbounded memory growth over long server runtimes.
  - Absence of explicit `threading.Lock` around dictionary mutations (`setdefault`, `append`) in multi-threaded Uvicorn/gRPC workers risks dict size mutation during iteration runtime errors.
- **Edge Cases:** Empty metric values; metric scrapings prior to any recorded transaction; non-numeric values passed to counters.
- **Scientific Claim Being Made:** Metrics rendered adhere strictly to the Prometheus Text-Based Exposition Standard (v0.0.4) and represent unbiased cumulative sample statistics.
- **Appropriate Verification Methodology:** Property-based testing for bucket monotonicity and memory growth stress testing under $10^6$ sample recordings.

---

### Component 2: `MetricProxy` — Dynamic Telemetry Interface Adapter
- **Module:** `app/infrastructure/telemetry/__init__.py`
- **Purpose:** Bridges Prometheus metric client semantics (`.set()`, `.inc()`, `.dec()`, `.add()`, `.observe()`, `.record()`, `.labels()`) to internal `TelemetryRegistry` backing storage.
- **Mathematical / Statistical Formulation:**
  - Gauge Mutation: $G_t = v$
  - Counter Increment: $C_{t} = C_{t-1} + \delta$
  - Counter Decrement: $C_{t} = C_{t-1} - \delta$
- **Monitoring Claim:** Simulates native `prometheus_client` objects for domain modules (`cfi_concept_drift_psi`, `cfi_feature_drift_ks_stat`, `cfi_model_brier_score`, etc.) without hard external dependencies.
- **Expected Invariant:** Invoking `.inc(x)` followed by `.dec(x)` restores counter to prior value: $C_{t+2} = C_t$.
- **Possible Implementation Risks:**
  - `.dec()` allows counters to become negative, violating standard Prometheus counter semantics where counters must be monotonically non-decreasing (counters that decrease break rate calculation functions like `rate()` in Prometheus).
- **Edge Cases:** Calling `.dec()` on an uninitialized counter; chaining `.labels()` with mismatched label keys.
- **Scientific Claim Being Made:** Provide API compatibility with standard Python Prometheus SDKs while maintaining zero-dependency fallback capability.
- **Appropriate Verification Methodology:** Unit tests verifying state equivalence between `MetricProxy` and official `prometheus_client` primitives.

---

### Component 3: `track_fl_round` Decorator — Training Round Telemetry Harvester
- **Module:** `app/infrastructure/telemetry/__init__.py`
- **Purpose:** Measures execution duration and participant node count of federated learning training functions and records metrics to `TelemetryRegistry`.
- **Mathematical / Statistical Formulation:**
  - Duration: $\Delta t = t_{\text{end}} - t_{\text{start}}$
  - Participant Count: $P = |\text{participants}|$
- **Monitoring Claim:** Captures wall-clock training round latency and participating bank count automatically upon function completion.
- **Expected Invariant:** $\Delta t > 0.0$ and $P \ge 0$.
- **Possible Implementation Risks:**
  - If the wrapped function raises an unhandled exception, `start` time is captured but `record_fl_round` is bypassed, creating a telemetry gap for failed rounds.
  - Relies on structural sub-typing heuristics (`isinstance(res, dict)` and `"participants" in res`) to extract node count; functions returning non-conforming structures silently record $P = 0$.
- **Edge Cases:** Wrapped function returning `None`, empty list, or non-dictionary object; function raising runtime exception.
- **Scientific Claim Being Made:** Accurately reflects exact round execution wall-clock time without modifying wrapped function logic.
- **Appropriate Verification Methodology:** Integration tests comparing decorator-measured latency against external system timers under success and failure outcomes.

---

### Component 4: `track_grpc_latency` Decorator — Transport Latency Telemetry
- **Module:** `app/infrastructure/telemetry/__init__.py`
- **Purpose:** Measures execution time of gRPC handler functions and categorizes status as `"OK"` or `"ERROR"`.
- **Mathematical / Statistical Formulation:**
  - Latency: $\Delta t = t_{\text{completion}} - t_{\text{invocation}}$
  - Categorization: $\text{Status} = \begin{cases} \text{"OK"} & \text{if no exception raised} \\ \text{"ERROR"} & \text{if exception raised} \end{cases}$
- **Monitoring Claim:** Captures gRPC method invocation latencies and status breakdown in histogram buckets.
- **Expected Invariant:** Every gRPC invocation records exactly one histogram sample in `finally` block regardless of exception status.
- **Possible Implementation Risks:**
  - Wraps synchronous functions using `time.time()`; if applied to asynchronous `async def` gRPC handlers without `await` handling, it measures coroutine creation time rather than total async execution duration.
- **Edge Cases:** High-frequency RPC calls ($> 10,000$ req/sec); async handler wrapping.
- **Scientific Claim Being Made:** Guarantees complete observation of all gRPC transport latencies with zero dropped call samples.
- **Appropriate Verification Methodology:** Concurrency benchmarking verifying 100% sample capture under multi-threaded gRPC workloads.

---

### Component 5: `OpenTelemetryTracer` — W3C Trace Context Propagation Engine
- **Module:** `app/infrastructure/telemetry/otel_tracer.py`
- **Purpose:** Manages W3C Trace Context headers (`traceparent`, `tracestate`) across distributed coordinator services and remote bank daemons.
- **Mathematical / Statistical Formulation:**
  - Trace ID: $T \in \{0..2^{128}-1\}$, rendered as 32-character hex string ($128 \text{ bits}$).
  - Span ID: $S \in \{0..2^{64}-1\}$, rendered as 16-character hex string ($64 \text{ bits}$).
  - W3C Format: `00-{trace_id}-{span_id}-01` (Version 00, Sampled flag `01`).
- **Monitoring Claim:** Complies with W3C Trace Context specification for end-to-end distributed transaction tracing.
- **Expected Invariant:**
  - $\text{len}(\text{trace\_id}) = 32$ and $\text{len}(\text{span\_id}) = 16$.
  - Extracted trace ID matches injected trace ID across network boundaries: $T_{\text{extracted}} = T_{\text{injected}}$.
- **Possible Implementation Risks:**
  - Generates random IDs using `random.getrandbits()` instead of cryptographically secure `secrets` or standard OTel ID generators, creating a potential trace ID collision risk at high volume.
  - Header extraction fallback generates a new trace ID on malformed headers, breaking trace continuity without logging a trace context corruption warning.
- **Edge Cases:** Malformed `traceparent` string missing hyphens; non-hex characters in header; missing `tracestate`.
- **Scientific Claim Being Made:** Formats and parses distributed tracing context in strict compliance with W3C Trace Context Recommendation (2021).
- **Appropriate Verification Methodology:** Spec compliance tests validating header generation against W3C test vectors.

---

### Component 6: Stage-Specific Trace Spans — Multi-Stage Pipeline Instrumentation
- **Module:** `app/infrastructure/telemetry/otel_tracer.py`
- **Purpose:** Context manager helpers for tracing 6 discrete pipeline stages: `ingest_transaction`, `feature_store_aggregation`, `local_pytorch_training`, `grpc_mtls_transmit`, `central_parameter_aggregation`, and `model_registry_save`.
- **Mathematical / Statistical Formulation:**
  - Span Duration: $D = (t_{\text{exit}} - t_{\text{entry}}) \times 1000.0\,\text{ms}$
- **Monitoring Claim:** Captures granular stage-by-stage latency breakdowns across the entire federated learning execution lifecycle.
- **Expected Invariant:** Total trace duration satisfies $D_{\text{total}} \ge \sum D_{\text{sequential\_stages}}$.
- **Possible Implementation Risks:**
  - `ingest_transaction_span` and helper methods immediately yield the result of a context manager block rather than returning an active context manager to be used via `with` statement by callers, causing spans to close prematurely.
- **Edge Cases:** Exception raised within nested span; multi-threaded stage execution.
- **Scientific Claim Being Made:** Provides exact stage-level latency attribution for federated training workflows.
- **Appropriate Verification Methodology:** End-to-end trace tree verification checking parent-child span relationships and timing hierarchy.

---

### Component 7: Hardware & Training Telemetry Recorders
- **Module:** `app/infrastructure/telemetry/otel_tracer.py`
- **Purpose:** Formats node-level CPU, RAM, GPU memory utilization, training loss, communication latency, and DP epsilon into structured dictionaries for Prometheus scrapes.
- **Mathematical / Statistical Formulation:**
  - Timestamp formatting: ISO 8601 UTC string `YYYY-MM-DD HH:MM:SSZ`.
- **Monitoring Claim:** Standardizes system resource utilization and training progress metrics for observability backends.
- **Expected Invariant:** $0.0 \le \text{cpu\_percent} \le 100.0$, $\text{ram\_mb} \ge 0.0$, $\text{dp\_epsilon} \ge 0.0$.
- **Possible Implementation Risks:**
  - Does not directly query OS kernel APIs (`psutil` or `pynvml`); relies on caller passing accurate float values.
- **Edge Cases:** Systems without GPU (returns default `0.0` GPU memory); negative loss values.
- **Scientific Claim Being Made:** Formats system utilization metrics deterministically for cross-platform telemetry ingestion.
- **Appropriate Verification Methodology:** Schema verification ensuring generated dictionary structures match expected Prometheus exporter schemas.

---

### Component 8: `SIEMLogExporter` — Multi-Format Security Audit Log Forwarder
- **Module:** `app/infrastructure/logging/siem_exporter.py`
- **Purpose:** Formats security audit events into Syslog (RFC 5424), Common Event Format (CEF), Splunk HEC JSON, or Datadog Log JSON, and forwards them over UDP/TCP or HTTP.
- **Mathematical / Statistical Formulation:**
  - RFC 5424 Priority Calculation: $\text{PRI} = \text{Facility} \times 8 + \text{Severity} = 16 \times 8 + 6 = 134$ (`local0.notice`).
  - CEF Severity Mapping:
    $$\text{Severity}_{\text{CEF}}(S) = \begin{cases} 1 & \text{LOW} \\ 4 & \text{MEDIUM} \\ 7 & \text{HIGH} \\ 10 & \text{CRITICAL} \end{cases}$$
- **Monitoring Claim:** Formats audit events into valid RFC 5424 Syslog, CEF, Splunk HEC, and Datadog JSON structures and delivers them over secure network protocols.
- **Expected Invariant:**
  - Syslog message begins with `<134>1`.
  - CEF message adheres to `CEF:0|CFI|Simulator|2.0|...` header structure.
- **Possible Implementation Risks:**
  - Network calls (`socket.sendto`, `urllib.request.urlopen`) block caller thread unless executed asynchronously.
  - Syslog fallback connects via TCP 6514 without TLS wrapping when UDP 514 fails, transmitting audit events unencrypted over the network if network path is insecure.
- **Edge Cases:** Unreachable SIEM endpoint; unconfigured API tokens; network socket timeouts.
- **Scientific Claim Being Made:** Guarantees audit event structural compliance across major enterprise SIEM ingestion standards.
- **Appropriate Verification Methodology:** Packet capture (pcap) inspection and validator parsing against RFC 5424 and ArcSight CEF specifications.

---

### Component 9: Offline SIEM Retry Queue Buffer & Background Flusher
- **Module:** `app/infrastructure/logging/siem_exporter.py`
- **Purpose:** Provides zero-data-loss audit logging by writing failed SIEM events to a local JSONL file (`siem_retry_queue.jsonl`) and flushing them via a background daemon thread.
- **Mathematical / Statistical Formulation:**
  - Queue Length: $L_t = L_{t-1} + N_{\text{failed}} - N_{\text{flushed}}$
- **Monitoring Claim:** Guarantees security audit event preservation during network outages or SIEM endpoint downtime.
- **Expected Invariant:** Every event passed to `export()` is either successfully delivered to an active exporter or appended to `siem_retry_queue.jsonl`.
- **Possible Implementation Risks:**
  - Concurrent file writes to `siem_retry_queue.jsonl` from multiple process workers lack file-locking primitives (`fcntl.flock` or `msvcrt.locking`), leading to corrupted JSONL lines.
  - Disk storage for retry queue is unbounded; prolonged network outage can exhaust disk space.
- **Edge Cases:** Read/write permissions errors on disk; partial JSON line writes due to abrupt process kill; corrupted JSON lines during flush.
- **Scientific Claim Being Made:** Provides eventual delivery guarantees ($\text{RPO} \to 0$) for security audit logs across transient network failures.
- **Appropriate Verification Methodology:** Fault injection testing cutting network interface for 1 hour while generating 1,000 audit events, verifying 100% recovery post-reconnection.

---

### Component 10: `RealtimeSLAMonitor` — Latency Quantile Interpolation Engine
- **Module:** `app/application/services/sla_monitor.py`
- **Purpose:** Computes real-time p50, p95, and p99 latency percentiles and tracks SLA compliance percentage against a configured target threshold ($T_{\text{SLA}} = 100.0\,\text{ms}$).
- **Mathematical / Statistical Formulation:**
  - Sample Sorting: $x_{(1)} \le x_{(2)} \le \dots \le x_{(n)}$
  - Rank Index: $k = (n - 1) \cdot \frac{p}{100}, \quad f = \lfloor k \rfloor, \quad c = \lceil k \rceil$
  - Percentile Interpolation:
    $$Q(p) = x_{(f)} \cdot (c - k) + x_{(c)} \cdot (k - f)$$
  - Compliance Percentage:
    $$\text{Compliance } \% = \frac{n - V}{n} \times 100.0$$
    where $V = \sum_{i=1}^n \mathbb{I}(x_i > T_{\text{SLA}})$.
- **Monitoring Claim:** Computes exact continuous linear-interpolation percentiles for latency distributions.
- **Expected Invariant:**
  - $x_{(1)} \le Q(50) \le Q(95) \le Q(99) \le x_{(n)}$.
  - $0.0 \le \text{Compliance } \% \le 100.0$.
- **Possible Implementation Risks:**
  - Stores all latency samples in an in-memory list (`self._latencies`). Memory grows linearly $\mathcal{O}(N)$ over time, causing high memory overhead and increasing $O(N \log N)$ sorting latency on summary computation.
  - Standard production percentile tracking should use bounded streaming quantile algorithms (e.g., t-digest or P2 algorithm) rather than full sample array sorting.
- **Edge Cases:** Empty latency array (returns 0.0 percentiles and 100% compliance); single latency sample; all samples breaching SLA threshold.
- **Scientific Claim Being Made:** Implements continuous linear quantile interpolation matching NIST/GSL statistical definitions.
- **Appropriate Verification Methodology:** Property-based testing comparing `_percentile()` against `numpy.percentile(method='linear')` across 1,000 randomized arrays.

---

### Component 11: `TenantMeteringService` — Resource Quota & Usage Tracker
- **Module:** `app/application/services/tenant_metering.py`
- **Purpose:** Tracks real-time tenant resource consumption (daily inferences, monthly FL rounds, storage MB), resets daily metrics at UTC midnight, and enforces resource quota bounds.
- **Mathematical / Statistical Formulation:**
  - Daily Usage Reset Predicate:
    $$\text{Reset}(t) = \mathbb{I}\left(\text{date}_{\text{UTC}}(t) \neq \text{last\_reset\_date}\right)$$
  - Quota Violation Condition:
    $$\text{Violation}(u, L) = (u_{\text{inf}} \ge L_{\text{inf}}) \lor (u_{\text{fl}} \ge L_{\text{fl}}) \lor (u_{\text{store}} \ge L_{\text{store}})$$
- **Monitoring Claim:** Accurately enforces multi-tenant resource boundary isolation and daily usage resets.
- **Expected Invariant:**
  - Usage metrics reset to 0 at UTC midnight: $\text{daily\_inferences} = 0$ when date rolls over.
  - Quota checks return `False` if usage $\ge$ configured limit.
- **Possible Implementation Risks:**
  - In-memory usage storage (`self._usage`) is not persisted to PostgreSQL or Redis. Restarting the backend process resets all daily tenant usage counters to zero, allowing tenants to bypass daily quotas.
- **Edge Cases:** UTC date rollover during high-throughput inference stream; negative count increments; non-existent tenant ID.
- **Scientific Claim Being Made:** Guarantees deterministic tenant resource boundary enforcement under continuous operation.
- **Appropriate Verification Methodology:** Clock manipulation testing simulating UTC date transitions and validating counter resetting.

---

### Component 12: Tenant Billing Cost Estimator
- **Module:** `app/application/services/tenant_metering.py`
- **Purpose:** Calculates estimated tenant billing charges based on daily inference volume and monthly federated learning round participation.
- **Mathematical / Statistical Formulation:**
  $$\text{Cost}_{\text{USD}} = (U_{\text{inf}} \times \$0.001) + (U_{\text{fl}} \times \$10.00)$$
- **Monitoring Claim:** Computes accurate, transparent usage-based billing estimates for dashboard display and invoicing.
- **Expected Invariant:** $\text{Cost}_{\text{USD}} \ge 0.0$, rounded to 2 decimal places.
- **Possible Implementation Risks:**
  - Hardcodes rates ($0.001 per inference, $10.00 per FL round) inside Python code rather than loading rates from configuration or database tariff tables.
- **Edge Cases:** Zero usage ($0.00 USD); high volume ($10^7$ inferences).
- **Scientific Claim Being Made:** Computes exact linear billing metrics proportional to consumption.
- **Appropriate Verification Methodology:** Numerical verification checking output accuracy against manual arithmetic evaluation.

---

### Component 13: `AlertIntelligenceService` — Risk Score Scaler & Severity Classifier
- **Module:** `app/application/services/alert_service.py`
- **Purpose:** Scales probability prediction scores $[0, 1]$ to integer risk scores $[0, 1000]$ and classifies severity into 5 discrete tiers.
- **Mathematical / Statistical Formulation:**
  - Scaled Risk Score: $R = \text{round}(s \times 1000, 1), \quad s \in [0.0, 1.0]$
  - Severity Classification:
    $$\text{Severity}(s) = \begin{cases} \text{CRITICAL} & s \ge 0.90 \\ \text{HIGH} & s \ge 0.75 \\ \text{MEDIUM} & s \ge 0.50 \\ \text{LOW} & s \ge 0.30 \\ \text{INFO} & s < 0.30 \end{cases}$$
- **Monitoring Claim:** Standardizes continuous model risk outputs into discrete, actionable alert severity tiers.
- **Expected Invariant:** $0.0 \le R \le 1000.0$ and $\text{Severity}(s_1) \ge \text{Severity}(s_2)$ for $s_1 \ge s_2$.
- **Possible Implementation Risks:**
  - Threshold boundaries are hardcoded; cannot be dynamically adjusted per bank risk appetite without code modification.
- **Edge Cases:** Boundary values ($s = 0.90, 0.75, 0.50, 0.30$); score outside $[0, 1]$ range.
- **Scientific Claim Being Made:** Monotonically maps continuous risk probabilities to discrete decision tiers without order inversions.
- **Appropriate Verification Methodology:** Boundary testing across $[0.0, 1.0]$ spectrum.

---

### Component 14: Anonymized Shared Intelligence Publisher
- **Module:** `app/application/services/alert_service.py`
- **Purpose:** Converts internal fraud alerts into anonymized shared intelligence items using privacy-preserving hashes (HMAC-SHA256) for cross-bank correlation without PII leakage.
- **Mathematical / Statistical Formulation:**
  - Privacy Hash: $H = \text{HMAC-SHA256}(K, \text{tx\_id} \mathbin{\Vert} \text{"transaction"})$
  - Risk Indicator: $I = \frac{R}{1000} \in [0.0, 1.0]$
- **Monitoring Claim:** Publishes cross-institutional risk signals while mathematically guaranteeing zero PII exposure.
- **Expected Invariant:**
  - No raw transaction ID, customer ID, or account details present in published intelligence structure.
  - Anonymized hash is deterministic for identical input transaction ID and secret key.
- **Possible Implementation Risks:**
  - Uses in-memory Redis emulation list (`"intelligence_list"`). List grows indefinitely without TTL expiration or sliding window trimming.
- **Edge Cases:** Duplicate alert publishing; cross-bank self-consumption filter validation.
- **Scientific Claim Being Made:** Provides zero-knowledge risk indicator sharing satisfying GDPR/PSD2 privacy constraints.
- **Appropriate Verification Methodology:** Differential privacy / entropy audit verifying raw inputs cannot be reconstructed from published hashes.

---

### Component 15: Cross-Alert Intelligence Correlation Engine
- **Module:** `app/application/services/alert_service.py`
- **Purpose:** Correlates fraud alerts across institutions to detect multi-bank entity overlap and short-window velocity bursts ($\Delta t < 60\,\text{s}$).
- **Mathematical / Statistical Formulation:**
  - Entity Overlap Predicate:
    $$\text{Overlap}(e) = \mathbb{I}\left(|\{a \in \text{Alerts} : e \in a.\text{entities}\}| \ge 2\right)$$
  - Velocity Burst Predicate:
    $$\text{Velocity Burst}(a_i, a_j) = \mathbb{I}\left(a_i.\text{bank} = a_j.\text{bank} \land (t_j - t_i < 60\,\text{s})\right)$$
- **Monitoring Claim:** Automatically identifies coordinated cross-bank fraud patterns and high-velocity attack bursts.
- **Expected Invariant:** Alerts flagged for velocity burst must originate from the same bank and possess timestamp delta $< 60.0\,\text{s}$.
- **Possible Implementation Risks:**
  - Velocity analysis sorts all alerts in memory using $\mathcal{O}(N \log N)$ sort; performance degrades with large alert volumes.
- **Edge Cases:** Simultaneous alerts ($t_j - t_i = 0\,\text{s}$); alerts across year boundaries; empty alert list.
- **Scientific Claim Being Made:** Accurately clusters temporal and relational fraud signals across distributed alert streams.
- **Appropriate Verification Methodology:** Synthetic pattern injection testing injecting known velocity and entity overlap cascades.

---

### Component 16: `SupportDiagnosticCompiler` — PII Redaction Engine
- **Module:** `app/application/services/support_diagnostics.py`
- **Purpose:** Sanitizes raw system log strings by redacting PII patterns (IBANs, email addresses) before bundle packaging.
- **Mathematical / Statistical Formulation:**
  - IBAN Regex Pattern: `TR\d{24}`
  - Email Regex Pattern: `\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b`
  - Redaction Mapping: $S_{\text{sanitized}} = \text{RegexReplace}(S_{\text{raw}}, \text{Pattern}, \text{"[REDACTED]"})$
- **Monitoring Claim:** Removes sensitive customer identity data from diagnostic support exports.
- **Expected Invariant:** No unmasked IBAN matching `TR\d{24}` or valid email string exists in sanitized log text.
- **Possible Implementation Risks:**
  - Regex patterns cover only Turkish IBANs (`TR\d{24}`) and standard emails. International IBANs (e.g., DE, FR, GB), credit card numbers (PAN), national IDs, and phone numbers are not matched and remain unredacted.
- **Edge Cases:** Mixed-case emails; whitespace variations in IBAN strings; malformed PII tokens.
- **Scientific Claim Being Made:** Guarantees deterministic removal of targeted PII regex classes from diagnostic exports.
- **Appropriate Verification Methodology:** Adversarial test suite passing 10,000 synthetic logs containing diverse PII formats and asserting 100% redaction rate.

---

### Component 17: Support Diagnostic Checksum Manifest
- **Module:** `app/application/services/support_diagnostics.py`
- **Purpose:** Generates a SHA-256 cryptographic digest of compiled diagnostic JSON bundles to ensure file integrity during transmission to support engineers.
- **Mathematical / Statistical Formulation:**
  - Digest: $H_{\text{bundle}} = \text{SHA-256}(\text{Bytes}_{\text{JSON}})$
- **Monitoring Claim:** Provides cryptographic tamper-evidence for diagnostic telemetry bundles.
- **Expected Invariant:** Any single-bit alteration in `support_bundle_*.json` alters `checksum_sha256`.
- **Possible Implementation Risks:**
  - Bundle JSON includes non-deterministic fields like `uuid.uuid4()` and `datetime.now(UTC)`, meaning two bundles compiled from identical system state produce different checksums.
- **Edge Cases:** Disk write failure during bundle output; empty log buffer compilation.
- **Scientific Claim Being Made:** Ensures bit-exact transmission integrity validation via SHA-256 digests.
- **Appropriate Verification Methodology:** Bit-flip verification confirming checksum invalidation on payload mutation.

---

### Component 18: `MetricsService` — Aggregate Model Improvement Evaluator
- **Module:** `app/application/services/metrics_service.py`
- **Purpose:** Converts raw model evaluation dicts into domain `EvaluationMetrics` objects and calculates mean metric deltas ($\Delta = \text{federated} - \text{local}$).
- **Mathematical / Statistical Formulation:**
  $$\Delta_m = \frac{1}{N} \sum_{i=1}^N \left(M_{\text{federated}, i}^{(m)} - M_{\text{local}, i}^{(m)}\right), \quad m \in \{\text{acc}, \text{prec}, \text{rec}, \text{f1}, \text{auc}\}$$
- **Monitoring Claim:** Quantifies the net performance gain of global federated models relative to isolated local models across participating institutions.
- **Expected Invariant:** Positive delta $\Delta_m > 0$ strictly indicates federated model superiority for metric $m$.
- **Possible Implementation Risks:**
  - Uses `zip(local_metrics, federated_metrics, strict=False)`. If list lengths differ, extra metrics in the longer list are silently ignored without raising a length mismatch exception.
- **Edge Cases:** Empty metrics lists (returns `{}`); mismatched metric list lengths; zero variance metrics.
- **Scientific Claim Being Made:** Computes unbiased sample mean deltas across institutional evaluation pairs.
- **Appropriate Verification Methodology:** Reference verification comparing output against `numpy.mean()` across synthetic metric pairs.

---

### Component 19: `/drift/analyze` Endpoint — Real-Time Statistical Drift Exporter
- **Module:** `app/presentation/routers/monitoring.py`
- **Purpose:** Executes statistical feature drift (KS-test, Wasserstein, PSI) and concept drift analysis, exposing metrics via REST API and recording values to Prometheus gauge proxies.
- **Mathematical / Statistical Formulation:**
  - Kolmogorov-Smirnov Statistic: $D = \sup_x |F_1(x) - F_2(x)|$
  - Population Stability Index (PSI):
    $$\text{PSI} = \sum_{b=1}^{B} \left(P_b - Q_b\right) \times \ln\left(\frac{P_b}{Q_b}\right)$$
- **Monitoring Claim:** Exposes real-time statistical drift analysis and updates Prometheus drift gauges (`cfi_concept_drift_psi`, `cfi_feature_drift_ks_stat`).
- **Expected Invariant:** $D \in [0.0, 1.0]$ and $\text{PSI} \ge 0.0$.
- **Possible Implementation Risks:**
  - Uses hardcoded `np.random.seed(42)` synthetic reference data inside endpoint module scope when real data is not supplied, generating static synthetic drift reports rather than evaluating production database samples.
- **Edge Cases:** Zero-variance feature vectors; empty current transaction arrays; divide-by-zero in PSI log ratio when bucket count $Q_b = 0$.
- **Scientific Claim Being Made:** Implements standard non-parametric goodness-of-fit tests (KS) and information-theoretic divergence metrics (PSI) for runtime model monitoring.
- **Appropriate Verification Methodology:** Statistical verification checking KS p-values and PSI values against `scipy.stats.ks_2samp` and reference R implementation.

---

### Component 20: Model Probability Calibration Exporter
- **Module:** `app/presentation/routers/monitoring.py`
- **Purpose:** Computes Brier score, Expected Calibration Error (ECE), and Maximum Calibration Error (MCE) across 10 probability bins and exports reliability curve data.
- **Mathematical / Statistical Formulation:**
  - Brier Score: $\text{BS} = \frac{1}{N} \sum_{i=1}^N (p_i - y_i)^2$
  - ECE: $\text{ECE} = \sum_{b=1}^B \frac{|B_b|}{N} |\text{acc}(B_b) - \text{conf}(B_b)|$
- **Monitoring Claim:** Evaluates model probability calibration and reliability curve points for production monitoring.
- **Expected Invariant:** $0.0 \le \text{BS} \le 1.0$ and $0.0 \le \text{ECE} \le 1.0$.
- **Possible Implementation Risks:**
  - Endpoint evaluates fixed sample arrays (`_sample_labels`, `_sample_probs`) defined at module import time rather than accepting live production inference predictions from database logs.
- **Edge Cases:** All-zero predictions; empty evaluation bins; extreme probability outputs ($p \in \{0, 1\}$).
- **Scientific Claim Being Made:** Quantifies prediction probability reliability according to DeGroot-Fienberg calibration bounds.
- **Appropriate Verification Methodology:** Cross-verification against `sklearn.metrics.brier_score_loss` and Scikit-Plot calibration curves.

---

### Component 21: Prometheus Alertmanager Active Alerts Feed
- **Module:** `app/presentation/routers/monitoring.py`
- **Purpose:** Returns active firing and resolved alerts for system monitoring dashboards (`GET /api/v1/monitoring/alerts`).
- **Mathematical / Statistical Formulation:** N/A (JSON Schema Serialization).
- **Monitoring Claim:** Exposes active system alerts formatted for frontend monitoring consumption.
- **Expected Invariant:** Each alert object contains valid `alert_name`, `severity`, `started_at` ISO timestamp, and `status` (`firing` or `resolved`).
- **Possible Implementation Risks:**
  - Hardcodes a static list of 2 sample responses (`SignificantConceptDrift`, `HighGatewayLatency`) directly inside the endpoint body rather than querying Prometheus Alertmanager API (`/api/v2/alerts`).
- **Edge Cases:** Alertmanager unreachable; zero active alerts.
- **Scientific Claim Being Made:** Provides real-time visibility into active operational alerts firing across the cluster.
- **Appropriate Verification Methodology:** Mock API integration test asserting endpoint response structure matches Prometheus Alertmanager v2 schema.

---

### Component 22: Automated Re-Training Trigger Dispatcher
- **Module:** `app/presentation/routers/monitoring.py`
- **Purpose:** Endpoint (`POST /api/v1/monitoring/drift/trigger-retrain`) to initiate an automated federated re-training round when concept drift thresholds are breached.
- **Mathematical / Statistical Formulation:**
  - Simulation ID format: `sim_auto_retrain_{timestamp}`.
- **Monitoring Claim:** Enables automated closed-loop model re-training upon detection of significant data drift.
- **Expected Invariant:** `triggered == True` and `new_simulation_id` is non-null.
- **Possible Implementation Risks:**
  - Endpoint generates a simulation ID string and logs an info message, but does not actually invoke `CoordinatorService.start_round()` or queue an asynchronous Celery task to execute real model re-training.
- **Edge Cases:** Concurrent trigger calls; invalid reason parameters.
- **Scientific Claim Being Made:** Provides automated orchestration triggering for model maintenance lifecycle management.
- **Appropriate Verification Methodology:** Integration test verifying task submission to Celery worker queue upon trigger invocation.

---

### Component 23: Liveness (`/health`) & Readiness (`/health/ready`) Probes
- **Module:** `app/presentation/routers/health.py`
- **Purpose:** Provides HTTP probes for Kubernetes readiness/liveness controllers, Docker health checks, and cloud load balancers.
- **Mathematical / Statistical Formulation:**
  $$\text{Readiness} = \bigwedge_{i \in \{\text{Redis}, \text{PostgreSQL}\}} \text{HealthCheck}_i()$$
- **Monitoring Claim:** Accurately reflects service viability and downstream dependency connectivity (PostgreSQL & Redis).
- **Expected Invariant:**
  - `/health` returns HTTP 200 `{"status": "healthy"}` if API process is running.
  - `/health/ready` returns `"status": "ready"` if both Redis and DB are connected, or `"degraded"` if any dependency fails.
- **Possible Implementation Risks:**
  - Database health check executes `SELECT 1` on every readiness probe. High-frequency probe scraping (e.g. every 1s from multiple load balancers) can saturate database connection pools.
- **Edge Cases:** Database connection timeout; Redis cluster failover during probe; pool exhaustion.
- **Scientific Claim Being Made:** Adheres to standard Cloud-Native Kubernetes Probe specifications (v1.28).
- **Appropriate Verification Methodology:** Dependency fault injection cutting database and Redis connections and verifying HTTP status and payload transitions.

---

### Component 24: Investigation Dashboard Aggregated Metrics & Risk Weights API
- **Module:** `app/presentation/routers/dashboard.py`
- **Purpose:** Aggregates real-time statistics across alerts, open cases, entities, shared intelligence, active scenarios, and graph clusters for dashboard visualization, and provides risk scoring weight management (`GET/PUT /api/v1/dashboard/risk-weights`).
- **Mathematical / Statistical Formulation:**
  - Total Alerts: $N_{\text{alerts}} = |\text{AlertStore}|$
  - Critical Alerts: $N_{\text{crit}} = \sum_{a \in \text{Alerts}} \mathbb{I}(a.\text{severity} = \text{CRITICAL})$
  - Open Cases: $N_{\text{cases}} = \sum_{c \in \text{Cases}} \mathbb{I}(c.\text{is\_open} = \text{True})$
- **Monitoring Claim:** Provides unified single-pane-of-glass operational metric aggregation across all subsystem data stores.
- **Expected Invariant:** $N_{\text{crit}} \le N_{\text{alerts}}$ and sum of alerts by severity equals total alerts: $\sum_{s} N_{\text{severity}(s)} = N_{\text{alerts}}$.
- **Possible Implementation Risks:**
  - `dashboard_stats()` fetches up to 1,000 alerts, cases, and entities into Python memory on every request, executing in-memory filtering rather than running SQL/Redis count aggregation queries (`COUNT(*)`), creating linear latency scaling $\mathcal{O}(N)$ on dashboard refreshes.
- **Edge Cases:** Zero alerts in database; uninitialized graph engine; invalid weight configuration payload.
- **Scientific Claim Being Made:** Displays exact, un-truncated counts of active system security objects without sample approximation.
- **Appropriate Verification Methodology:** End-to-end integration test verifying dashboard metrics match underlying database row counts exactly.

---

## 4. Capability Classification & Verification Summary Matrix

| ID | Telemetry Mechanism | Primary Target Module | Claimed Guarantee | Verification Status | Primary Defect / Limitation |
|:---:|:---|:---|:---|:---:|:---|
| **1** | Prometheus Metrics Exposition | `telemetry/__init__.py` | Standard Prometheus text format (v0.0.4) | **PARTIALLY SUPPORTED** | Unbounded list storage in histograms; no thread mutex locks |
| **2** | MetricProxy Adapter | `telemetry/__init__.py` | Zero-dependency Prometheus SDK adapter | **PARTIALLY SUPPORTED** | Allows negative counter decrements violating Prometheus rules |
| **3** | `track_fl_round` Decorator | `telemetry/__init__.py` | Automatic FL round latency & participant harvest | **SUPPORTED** | None — correctly measures wall-clock duration |
| **4** | `track_grpc_latency` Decorator | `telemetry/__init__.py` | gRPC transport latency & status capture | **SUPPORTED** | Sync-only execution model (not async await aware) |
| **5** | W3C Context Propagation Engine | `telemetry/otel_tracer.py` | W3C traceparent (00-id-id-01) spec compliance | **PARTIALLY SUPPORTED** | Uses pseudo-random `random.getrandbits()` instead of OTel IDs |
| **6** | Multi-Stage Pipeline Spans | `telemetry/otel_tracer.py` | 6-stage end-to-end trace span attribution | **PARTIALLY SUPPORTED** | Stage helpers yield inner context manager results directly |
| **7** | Hardware & Training Recorders | `telemetry/otel_tracer.py` | Resource utilization & FL metric formatting | **SUPPORTED** | Relies on caller passing accurate float values |
| **8** | Multi-Format SIEM Exporter | `logging/siem_exporter.py` | RFC 5424 Syslog, CEF, Splunk HEC, Datadog | **SUPPORTED** | TCP fallback uses unencrypted connection if UDP fails |
| **9** | Offline SIEM Retry Queue | `logging/siem_exporter.py` | Zero data loss audit logging via disk buffer | **PARTIALLY SUPPORTED** | File append lacks multi-process file locking (`flock`) |
| **10** | SLA Quantile Interpolation | `services/sla_monitor.py` | Continuous linear p50/p95/p99 quantile tracking | **PARTIALLY SUPPORTED** | Unbounded in-memory list sorting $\mathcal{O}(N \log N)$ |
| **11** | Tenant Quota Enforcement | `services/tenant_metering.py` | Daily/monthly boundary isolation & UTC reset | **PARTIALLY SUPPORTED** | In-memory usage lost on backend process restart |
| **12** | Tenant Billing Estimator | `services/tenant_metering.py` | Transparent usage-based billing calculation | **SUPPORTED** | Hardcodes rates inside Python code |
| **13** | Risk Score & Severity Classifier | `services/alert_service.py` | Monotonic 0-1000 scaling & 5-tier severity | **SUPPORTED** | Threshold boundaries are hardcoded |
| **14** | Anonymized Intelligence Publisher | `services/alert_service.py` | HMAC-SHA256 privacy hash sharing | **SUPPORTED** | Redis list storage lacks TTL expiration window |
| **15** | Cross-Alert Correlation Engine | `services/alert_service.py` | Multi-bank entity overlap & velocity (<60s) | **SUPPORTED** | In-memory sort scales $\mathcal{O}(N \log N)$ |
| **16** | PII Redaction Engine | `services/support_diagnostics.py` | Redacts IBANs and email addresses from logs | **PARTIALLY SUPPORTED** | Only redacts Turkish IBANs (`TR\d{24}`) and basic emails |
| **17** | Diagnostic Checksum Manifest | `services/support_diagnostics.py` | SHA-256 cryptographic digest manifest | **SUPPORTED** | Non-deterministic timestamps alter hash across runs |
| **18** | Aggregate Metric Delta Evaluator | `services/metrics_service.py` | Mean metric delta ($\Delta = \text{Fed} - \text{Local}$) | **SUPPORTED** | Non-strict zip ignores trailing elements |
| **19** | Statistical Drift Exporter | `routers/monitoring.py` | Real-time KS-test, Wasserstein, & PSI API | **PARTIALLY SUPPORTED** | Endpoint evaluates hardcoded random seed (seed=42) data |
| **20** | Model Calibration Exporter | `routers/monitoring.py` | Brier Score, ECE, & MCE exporter | **PARTIALLY SUPPORTED** | Evaluates fixed static arrays defined at import time |
| **21** | Alertmanager Active Alerts Feed | `routers/monitoring.py` | Prometheus Alertmanager v2 schema endpoint | **UNSUPPORTED** | Returns static hardcoded dummy alerts dictionary |
| **22** | Automated Retraining Trigger | `routers/monitoring.py` | Closed-loop retrain trigger on concept drift | **PARTIALLY SUPPORTED** | Generates simulation ID string but does not start round |
| **23** | Liveness & Readiness Probes | `routers/health.py` | Kubernetes `/health` & `/health/ready` probes | **SUPPORTED** | `SELECT 1` DB probe can load DB pool at high frequency |
| **24** | Dashboard Metrics & Risk API | `routers/dashboard.py` | Unified single-pane-of-glass stats aggregation | **SUPPORTED** | Fetches up to 1,000 items in memory rather than `COUNT(*)` |

---

## 5. Critical Implementation Deficiencies & Actionable Recommendations

### 5.1 Critical Deficiencies
1. **Mock Monitoring Router Implementations:**  
   Endpoints `/api/v1/monitoring/alerts` and `/api/v1/monitoring/drift/analyze` return hardcoded static data (`np.random.seed(42)` and dummy Alertmanager JSON). They must be wired to real database queries and live Alertmanager endpoints.
2. **Unbounded Telemetry Memory Growth:**  
   `TelemetryRegistry._histograms` and `RealtimeSLAMonitor._latencies` append float samples indefinitely to Python in-memory lists without truncation or sliding window caps, creating long-term memory leak risks.
3. **Volatile Tenant Usage Metrics:**  
   `TenantMeteringService` stores daily usage counters in memory without database persistence. Process restarts wipe tenant usage history, enabling quota bypass.
4. **Non-Thread-Safe Metric Registry:**  
   `TelemetryRegistry` dictionary mutations lack `threading.Lock` guards, risking dictionary mutation runtime errors during Prometheus scraping.

### 5.2 Recommended Actionable Remediation Plan
1. **Implement Sliding Window Buffers:** Replace raw Python list appends in `TelemetryRegistry` and `RealtimeSLAMonitor` with fixed-capacity `collections.deque(maxlen=10000)` or t-digest quantile estimators.
2. **Persist Tenant Usage to Redis/PostgreSQL:** Store tenant daily inference counts in Redis with a 24-hour TTL (`INCRBY tenant:usage:YYYY-MM-DD:inf`).
3. **Wire Monitoring Endpoints to Live Data:** Replace synthetic data generators in `monitoring.py` with queries against `ModelDriftService` using real database prediction tables.
4. **Add Thread Locks to Metric Registry:** Protect `TelemetryRegistry._gauges`, `_counters`, and `_histograms` with a `threading.Lock`.

---

*Scientific Verification Inventory — Telemetry & Observability Subsystem*  
*Privacy-Preserving Cross-Bank Fraud Detection using Federated Learning*  
*Version 1.0 — 2026-08-01*
