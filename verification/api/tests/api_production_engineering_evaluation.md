# Production Engineering & Reliability Evaluation — API Subsystem

This document provides an in-depth production engineering evaluation of the API subsystem. It analyzes 9 core architectural pillars: request consistency, error handling, response determinism, validation correctness, retry safety, idempotency, logging, observability, and backward compatibility.

---

## 1. Evaluation of Core Reliability Pillars

### 1.1 Request Consistency & Input Validation Correctness
* **Implementation Analysis:** Input validation is enforced at schema boundaries via Pydantic v2 data models. Numeric fields (`transaction_amount >= 0.0`, `hour_of_day ∈ [0, 23]`, `merchant_risk_score ∈ [0.0, 1.0]`) and string length limits (`max_length=256`) are validated prior to controller invocation.
* **Content-Type Protection:** `ContentTypeMiddleware` rejects non-JSON bodies on mutating endpoints (`POST`, `PUT`, `PATCH`) with `HTTP 415 Unsupported Media Type` before payload deserialization.
* **Enum Guarding:** Enum query parameter coercions (`severity`, `status`) are wrapped in `try/except ValueError` guards, returning structured `HTTP 422` responses with valid values rather than unhandled `HTTP 500` exceptions.

### 1.2 Error Handling & RFC 7807 Standardization
* **Standardized Format:** Global `@app.exception_handler(Exception)` intercepts unhandled runtime errors, formatting responses according to RFC 7807 (`application/problem+json`).
* **Error Fields:** Responses include `type` (error URI identifier), `title`, `status`, `detail`, and `instance` (request path).
* **Leak Prevention:** Internal Python stack traces and raw SQL exceptions are suppressed in production environments.

### 1.3 Deterministic Responses & Score Idempotency
* **Scoring Isolation:** `/api/v1/predict` reads the last-committed online feature snapshot (`get_online_features`). Rolling feature accumulation (`ingest_transaction`) is decoupled to `BackgroundTasks` and executed post-response.
* **Deterministic Output:** Identical transaction feature vectors generate identical ML predictions and composite risk scores without side-effect mutation during the request lifecycle.

### 1.4 Retry Safety & Case Creation Idempotency
* **Header Deduplication:** `POST /api/v1/cases` accepts an optional `Idempotency-Key` header.
* **Service Architecture:** `IdempotencyService` checks a 24-hour TTL cache (Redis-backed with thread-safe in-memory fallback). Repeated requests return the identical cached JSON response without creating duplicate cases.

### 1.5 Logging & SIEM Integration
* **Structured JSON Logging:** Telemetry logs utilize `python-json-logger`, outputting structured JSON lines with fields (`timestamp`, `level`, `traceparent`, `bank_id`, `client_ip`).
* **SIEM Resilience:** Security events are logged to `ImmutableAuditChain` and exported to SIEM pipelines via `SIEMExporter`, which buffers un-sent logs to `siem_retry_queue.jsonl` during network interruptions.

### 1.6 Observability & OpenMetrics Integration
* **Prometheus Instrumentation:** `prometheus-fastapi-instrumentator` collects `http_request_duration_seconds` histograms per endpoint, method, and status code.
* **Metrics Endpoint:** OpenMetrics text output is exposed at `GET /metrics`.
* **Distributed Tracing:** `W3CTraceContextMiddleware` extracts or generates W3C `traceparent` headers (`00-{trace_id}-{span_id}-01`), propagating distributed trace context across HTTP responses.

### 1.7 Backward Compatibility & Version Governance
* **RFC 8594 Headers:** `APIVersionLifecycleMiddleware` injects `X-API-Version`, `Deprecation`, and `Sunset` headers into HTTP responses.
* **OpenAPI Snapshot:** Offline OpenAPI 3.1.0 schema contracts are snapshot-versioned in `backend/storage/openapi/openapi_snapshot.json` to prevent interface regressions in CI/CD.

---

## 2. In-App Guarantees vs. Ingress Service Mesh Separation

The table below delineates responsibilities implemented natively within the application code versus those delegated to edge ingress controllers (Envoy, NGINX) or service meshes (Istio).

| Architectural Feature | Application Layer (FastAPI In-App) | Ingress Gateway / Service Mesh Layer (Envoy/Istio) |
|---|---|---|
| **mTLS Authentication** | Verifies `X-Client-Cert-SHA256` fingerprints & checks local CRL revocation lists at L7. | Terminates physical L4 TLS binary handshakes & performs PKI cert chain validation. |
| **Volumetric Rate Limiting** | Enforces sliding-window IP rate limiting (`DDoSProtectionMiddleware`) returning HTTP 429 & `Retry-After`. | Enforces global cluster-wide token-bucket rate limits across multi-region edge nodes. |
| **Circuit Breaking** | Implements local fallback stores (e.g. in-memory Feature Store & Redis fallback). | Outlier detection, active health checking, and automatic pod eviction. |
| **Trace Context** | Injects/propagates W3C `traceparent` HTTP headers (`W3CTraceContextMiddleware`). | Injects B3 / Jaeger span headers and captures sidecar network latency. |
| **Payload Size Limits** | Enforces Pydantic string bounds (`max_length=256`). | Enforces max request body size (e.g. `client_max_body_size 10m`). |

---

## 3. Architectural Limitations & Production Recommendations

1. **GIL Concurrency Bound Mitigation:** PyTorch CPU inference and composite risk scoring execute synchronous C++ code. Offloading via `asyncio.to_thread` preserves event loop responsiveness, but overall single-worker throughput remains bounded by CPU core allocation (`OMP_NUM_THREADS=2`). Production scaling requires multi-worker Uvicorn deployment.
2. **In-Memory State Persistence:** In-memory fallback stores (used when Redis is unavailable) maintain state only for the process lifetime. Production deployments must rely on multi-node Redis clusters.
3. **Database Connection Pooling:** Development uses SQLite in-memory; production environments should utilize PostgreSQL with PGBouncer connection pooling.

---

*This document completes the production engineering evaluation of the API subsystem.*
