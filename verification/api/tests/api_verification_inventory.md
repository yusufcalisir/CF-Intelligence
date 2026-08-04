# Scientific Verification Inventory — API Subsystem

This document provides a comprehensive scientific audit inventory of the API implementation for the Privacy-Preserving Cross-Bank Fraud Detection platform. It systematically analyzes every component, endpoint specification, request/response contract, invariant, risk, edge case, engineering claim, and appropriate verification methodology.

---

## 1. Middleware & System Infrastructure Inventory

### 1.1 Global Exception Handler (RFC 7807)

* **Component:** `app.main.global_exception_handler`
* **Purpose:** Ensures all unhandled runtime exceptions return structured JSON formatted per RFC 7807 (`application/problem+json`) when requested or as standard JSON error responses.
* **Endpoint Specification:** Global error interceptor across all HTTP endpoints.
* **Request/Response Contract:**
  * *Request:* Any failing HTTP request.
  * *Response:* `HTTP 500 Internal Server Error`, Content-Type: `application/problem+json` or `application/json`.
  * *Body:* `{"type": "https://cfi-platform.org/errors/<ExcName>", "title": "Internal Server Error", "status": 500, "detail": "<msg>", "instance": "<path>"}`.
* **Expected Invariant:** Unhandled exceptions NEVER leak plain-text stack traces or non-JSON responses.
* **Possible Implementation Risks:** Exception string details might inadvertently leak internal variable names if not sanitized.
* **Edge Cases:** Exceptions raised during streaming responses or WebSocket handshakes after headers are sent.
* **Engineering Claim Being Made:** "All unhandled runtime errors conform to RFC 7807 problem details contracts without exception."
* **Appropriate Verification Methodology:** Adversarial property testing injecting unexpected runtime exceptions across routes.

---

### 1.2 Content-Type Enforcement Middleware

* **Component:** `app.main.ContentTypeMiddleware`
* **Purpose:** Protects mutating endpoints (`POST`, `PUT`, `PATCH`) by enforcing `Content-Type: application/json`.
* **Endpoint Specification:** Global middleware targeting mutating routes (excluding exempt paths like `/docs`, `/openapi.json`, `/ws/`).
* **Request/Response Contract:**
  * *Request:* Mutating request with non-JSON or missing `Content-Type`.
  * *Response:* `HTTP 415 Unsupported Media Type`, Content-Type: `application/problem+json`.
  * *Body:* `{"type": ".../UnsupportedMediaType", "title": "Unsupported Media Type", "status": 415, "detail": "...", "received": "<ct>", "instance": "<path>"}`.
* **Expected Invariant:** Non-JSON bodies sent to mutating endpoints are rejected at the edge before route handlers execute.
* **Possible Implementation Risks:** Overly strict matching breaking multipart form-data file uploads if paths are not exempted.
* **Edge Cases:** Binary payloads (`application/octet-stream`), empty bodies, `text/xml`, `application/x-www-form-urlencoded`.
* **Engineering Claim Being Made:** "Mutating endpoints strictly reject unsupported media types with HTTP 415 before payload parsing."
* **Appropriate Verification Methodology:** Fuzz testing with arbitrary binary and non-JSON content-type headers.

---

### 1.3 Distributed W3C Trace Context Middleware

* **Component:** `app.main.W3CTraceContextMiddleware`
* **Purpose:** Implements OpenTelemetry W3C `traceparent` specification for cross-service distributed tracing.
* **Endpoint Specification:** Applied to all incoming HTTP requests.
* **Request/Response Contract:**
  * *Request Header:* `traceparent: 00-{trace_id}-{span_id}-{flags}` (optional).
  * *Response Header:* `traceparent: 00-{trace_id}-{span_id}-01`.
* **Expected Invariant:** Every HTTP response carries a valid, deterministic W3C `traceparent` header.
* **Possible Implementation Risks:** Malformed incoming `traceparent` headers causing regex/split indexing errors.
* **Edge Cases:** Headers with invalid version digits, incorrect hex lengths, or empty strings.
* **Engineering Claim Being Made:** "All API responses carry W3C-compliant distributed trace context headers."
* **Appropriate Verification Methodology:** Header injection testing with valid and malformed `traceparent` strings.

---

### 1.4 API Version Lifecycle Middleware

* **Component:** `app.main.APIVersionLifecycleMiddleware`
* **Purpose:** Attaches RFC 8594 `Deprecation` and `Sunset` headers along with `X-API-Version` to support version governance.
* **Endpoint Specification:** Global HTTP response modifier.
* **Request/Response Contract:**
  * *Response Headers:* `X-API-Version: v1`, `Deprecation: <HTTP-date>` (optional), `Sunset: <HTTP-date>` (optional).
* **Expected Invariant:** Every response header contains explicit API version identification.
* **Possible Implementation Risks:** Unset deprecation date configurations omitting RFC headers.
* **Edge Cases:** CORS preflight `OPTIONS` requests needing header exposure in `Access-Control-Expose-Headers`.
* **Engineering Claim Being Made:** "All API endpoints explicitly declare their version lifecycle metadata via RFC 8594 headers."
* **Appropriate Verification Methodology:** HTTP response header assertion across GET/POST/OPTIONS calls.

---

### 1.5 In-App mTLS Peer Verification Middleware

* **Component:** `app.main.MTLSVerificationMiddleware`
* **Purpose:** Validates X.509 client certificate fingerprints and CRL revocation status at the application layer.
* **Endpoint Specification:** Applied to sensitive banking routes (`/api/v1/predict`, `/api/v1/training`, `/api/v1/banks`).
* **Request/Response Contract:**
  * *Request Headers:* `X-SSL-Client-Verify: SUCCESS`, `X-Client-Cert-SHA256: <fingerprint>`.
  * *Response:* `HTTP 403 Forbidden` if verification fails or certificate fingerprint is revoked in CRL.
* **Expected Invariant:** Requests with invalid or revoked client certificate fingerprints are denied access.
* **Possible Implementation Risks:** Mismatch between proxy header naming and application middleware extraction keys.
* **Edge Cases:** Revoked serial numbers present in CRL, header spoofing when proxy is bypassed.
* **Engineering Claim Being Made:** "Sensitive banking routes enforce in-app mTLS peer validation against active CRLs."
* **Appropriate Verification Methodology:** Security audit tests sending revoked and unverified certificate headers.

---

### 1.6 L7 Application-Layer DDoS Protection Middleware

* **Component:** `app.main.DDoSProtectionMiddleware`
* **Purpose:** Implements sliding-window token bucket IP rate limiting for burst attack detection and flood prevention.
* **Endpoint Specification:** Applied to all incoming HTTP routes per client IP address.
* **Request/Response Contract:**
  * *Response (when throttled):* `HTTP 429 Too Many Requests`, Headers: `Retry-After: 10`, `X-DDoS-Throttled: true`.
  * *Body:* `{"type": ".../DDoSThrottled", "title": "Volumetric Flood Throttling Triggered", "status": 429, ...}`.
* **Expected Invariant:** Client IPs exceeding 100 requests within a 10-second window are throttled immediately.
* **Possible Implementation Risks:** Shared NAT IP addresses throttling multiple legitimate users simultaneously.
* **Edge Cases:** Rapid burst of 150 requests in 1 second, spoofed `X-Forwarded-For` headers.
* **Engineering Claim Being Made:** "The application layer protects against L7 volumetric floods using sliding-window rate limiting."
* **Appropriate Verification Methodology:** High-throughput concurrent burst testing to verify HTTP 429 enforcement.

---

### 1.7 Prometheus Telemetry Instrumentator

* **Component:** `app.infrastructure.telemetry.setup_telemetry`
* **Purpose:** Instruments FastAPI application using `prometheus-fastapi-instrumentator` for per-endpoint HTTP metrics.
* **Endpoint Specification:** `GET /metrics` (untracked, excluded from schema).
* **Request/Response Contract:**
  * *Response:* `HTTP 200 OK`, Content-Type: `text/plain; version=0.0.4; charset=utf-8`.
  * *Body:* OpenMetrics text format containing `http_request_duration_seconds` histograms and `http_requests_in_flight`.
* **Expected Invariant:** Endpoint latency histograms and request counters are automatically updated on every HTTP call.
* **Possible Implementation Risks:** High cardinality metric explosion if path parameters are untemplated.
* **Edge Cases:** Unregistered paths causing unbounded metric label generation (`should_ignore_untemplated=True` configured).
* **Engineering Claim Being Made:** "Per-endpoint request durations and in-flight counters are automatically tracked in OpenMetrics format."
* **Appropriate Verification Methodology:** Scraping `/metrics` after benchmark execution to assert metric existence.

---

## 2. Router & Endpoint Inventory

### 2.1 Health & Readiness Probes (`health.py`)

* **Component:** `app.presentation.routers.health`
* **Purpose:** Exposes Kubernetes liveness and readiness probe endpoints.
* **Endpoint Specification:**
  * `GET /health` (Liveness)
  * `GET /health/ready` (Readiness)
* **Request/Response Contract:**
  * `GET /health` → `HTTP 200 OK`, `{"status": "healthy", "version": "0.2.0"}`.
  * `GET /health/ready` → `HTTP 200 OK` (when Redis & DB are healthy) OR `HTTP 503 Service Unavailable` (`{"status": "degraded", "checks": {...}}`).
* **Expected Invariant:** `/health/ready` returns HTTP 503 whenever Redis or Database dependencies fail.
* **Possible Implementation Risks:** Slow health check queries causing probe timeouts under heavy load.
* **Edge Cases:** Redis connection timeout, PostgreSQL pool exhaustion.
* **Engineering Claim Being Made:** "The readiness probe returns HTTP 503 on dependency failure for Kubernetes orchestration."
* **Appropriate Verification Methodology:** Fault injection disabling Redis/DB and asserting HTTP 503 response.

---

### 2.2 Machine Learning Inference & Scoring (`predict.py`)

* **Component:** `app.presentation.routers.predict`
* **Purpose:** Real-time transaction fraud score evaluation, shadow challenger model execution, and feature store ingestion.
* **Endpoint Specification:**
  * `POST /api/v1/predict`
  * `POST /api/v1/score-transaction`
* **Request/Response Contract:**
  * *Request:* `TransactionPredictRequest` (`transaction_amount >= 0.0`, `merchant_category: max_length=256`, `hour_of_day: [0, 23]`, `country_code: max_length=256`).
  * *Response:* `TransactionPredictResponse` (`fraud_probability ∈ [0.0, 1.0]`, `risk_score ∈ [0, 1000]`, `policy_action`, `breakdown`).
* **Expected Invariant:**
  * `fraud_probability` is bounded in [0, 1].
  * PyTorch inference and risk scoring are offloaded via `asyncio.to_thread` to prevent event loop blocking.
  * Feature Store ingestion (`ingest_transaction`) is executed via `BackgroundTasks` after response dispatch, ensuring score idempotency.
* **Possible Implementation Risks:** Out-of-memory errors during concurrent PyTorch forward passes on large tensors.
* **Edge Cases:** Extremely large transaction amounts (`1e308`), invalid categorical values, missing optional fields.
* **Engineering Claim Being Made:** "ML inference is non-blocking, idempotent, and guaranteed to produce bounded risk scores."
* **Appropriate Verification Methodology:** Property-based testing with Hypothesis and threadpool concurrency benchmarks.

---

### 2.3 Alert Management & Intelligence (`alerts.py`)

* **Component:** `app.presentation.routers.alerts`
* **Purpose:** Fraud alert retrieval, filtering, explainability report extraction, and cross-bank shared threat intelligence.
* **Endpoint Specification:**
  * `GET /api/v1/alerts`
  * `POST /api/v1/alerts`
  * `GET /api/v1/alerts/{alert_id}`
  * `GET /api/v1/alerts/{alert_id}/explainability`
  * `GET /api/v1/alerts/intelligence`
* **Request/Response Contract:**
  * *Query Params:* `limit` (`ge=1, le=200`), `severity` (`AlertSeverity` enum), `status` (`AlertStatus` enum).
  * *Response:* Paginated alert list, SHAP/LIME feature contributions, or `HTTP 422` on invalid enum query parameters.
* **Expected Invariant:** Invalid query enum parameters return `HTTP 422 Unprocessable Entity` rather than unhandled `HTTP 500`.
* **Possible Implementation Risks:** Missing index on `bank_id` / `created_at` causing slow query pagination.
* **Edge Cases:** `severity=INVALID_STRING`, `limit=0`, `limit=1000`, non-existent `alert_id`.
* **Engineering Claim Being Made:** "Alert query parameters are guarded against invalid Enum values, returning HTTP 422."
* **Appropriate Verification Methodology:** Invalid enum query parameter injection testing.

---

### 2.4 Case Investigation & Idempotency (`cases.py`)

* **Component:** `app.presentation.routers.cases`
* **Purpose:** Case lifecycle management, status transitions, note attachments, and idempotent case creation.
* **Endpoint Specification:**
  * `GET /api/v1/cases`
  * `POST /api/v1/cases`
  * `PUT /api/v1/cases/{case_id}/status`
  * `POST /api/v1/cases/{case_id}/notes`
  * `POST /api/v1/cases/{case_id}/link-alert`
* **Request/Response Contract:**
  * *Header:* `Idempotency-Key` (optional string, max_length=256).
  * *Request Body:* `CaseCreateRequest` (`title: max_length=256`, `priority: max_length=64`).
  * *Response:* `CaseResponse`, `HTTP 422` on invalid status transition or invalid enum parameter.
* **Expected Invariant:** Replaying `POST /api/v1/cases` with the same `Idempotency-Key` returns the cached response without creating duplicate cases.
* **Possible Implementation Risks:** In-memory idempotency cache fallback leaking memory if TTL expiration fails.
* **Edge Cases:** Duplicate `Idempotency-Key` sent concurrently across two parallel threads.
* **Engineering Claim Being Made:** "Case creation processes Idempotency-Key headers with Redis-backed 24h TTL deduplication."
* **Appropriate Verification Methodology:** Concurrent replay testing using identical `Idempotency-Key` values.

---

### 2.5 Security, ABAC, & Audit Chain (`security.py`)

* **Component:** `app.presentation.routers.security`
* **Purpose:** Dynamic ABAC policy evaluation, SHA-256 audit chain verification, and subsystem security status reporting.
* **Endpoint Specification:**
  * `POST /security/abac/evaluate`
  * `POST /security/audit-chain/verify`
  * `GET /security/status`
* **Request/Response Contract:**
  * *Evaluate Request:* `UserClaims`, `ABACResource`, `action`.
  * *Evaluate Response:* `{"allowed": bool, "reason": str, "policy_name": str}`.
  * *Verify Response:* `{"is_valid": bool, "length": int, "latest_hash": str}`.
* **Expected Invariant:**
  * Cross-tenant bank access is strictly denied by ABAC.
  * The SHA-256 audit chain remains cryptographically valid (`is_valid = True`) across sequential appends.
* **Possible Implementation Risks:** Audit chain lock contention under high concurrent logging throughput.
* **Edge Cases:** Mis-matched bank clearance levels, corrupted audit chain block hashes.
* **Engineering Claim Being Made:** "ABAC tenant isolation is mathematically enforced and audit chains are tamper-evident."
* **Appropriate Verification Methodology:** Property-based invariant verification and multi-thread concurrency testing.

---

### 2.6 Gateway Proxy Router (`gateway.py`)

* **Component:** `app.presentation.routers.gateway`
* **Purpose:** Reverse proxying, OIDC JWT bearer authentication, API key verification, and RFC rate limit header injection.
* **Endpoint Specification:**
  * `ANY /{path:path}` (Passthrough reverse proxy)
* **Request/Response Contract:**
  * *Headers:* `Authorization: Bearer <token>` or `X-API-Key: <key>`.
  * *Response Headers:* `X-RateLimit-Limit`, `X-RateLimit-Remaining`, `X-RateLimit-Reset`.
  * *Response (on auth fail):* `HTTP 401 Unauthorized` or `HTTP 403 Forbidden`.
  * *Response (on rate limit):* `HTTP 429 Too Many Requests`, `Retry-After`.
* **Expected Invariant:** Every gateway proxy response carries explicit RFC rate limit headers.
* **Possible Implementation Risks:** Downstream service unavailability causing HTTP 502 Bad Gateway timeouts.
* **Edge Cases:** Expired JWT tokens, malformed API keys, downstream socket connection refused.
* **Engineering Claim Being Made:** "The gateway enforces OIDC/API-key auth, ABAC authorization, and returns RFC rate limit headers."
* **Appropriate Verification Methodology:** Adversarial JWT/API-key testing and rate limit bucket verification.

---

### 2.7 Federated Learning Simulation & Training (`simulation.py`, `training.py`)

* **Component:** `app.presentation.routers.simulation`, `app.presentation.routers.training`
* **Purpose:** Federated Learning simulation management, round execution, participant configuration, and model aggregation.
* **Endpoint Specification:**
  * `GET /api/v1/simulations`, `POST /api/v1/simulations`
  * `POST /api/v1/training/round`
* **Request/Response Contract:**
  * *Request:* Simulation parameters (`num_rounds`, `privacy_budget_epsilon`, `participant_banks`).
  * *Response:* `SimulationResponse` or training round execution metrics.
* **Expected Invariant:** FL training rounds preserve client differential privacy bounds (`epsilon <= max_epsilon`).
* **Possible Implementation Risks:** Long-running simulation rounds blocking HTTP worker threads if executed synchronously.
* **Edge Cases:** `num_rounds = 0`, `epsilon <= 0.0`, missing participant banks.
* **Engineering Claim Being Made:** "FL simulation endpoints enforce differential privacy budget constraints."
* **Appropriate Verification Methodology:** Differential privacy parameter boundary testing.

---

### 2.8 Entity Resolution & Risk Graph (`entities.py`, `graph.py`)

* **Component:** `app.presentation.routers.entities`, `app.presentation.routers.graph`
* **Purpose:** Cross-bank privacy-preserving entity resolution and GraphSAGE relationship network graph visualization.
* **Endpoint Specification:**
  * `GET /api/v1/entities`, `POST /api/v1/entities`
  * `GET /api/v1/graph`
* **Request/Response Contract:**
  * *Request:* Entity identifiers or graph node depth parameters.
  * *Response:* Entity matching confidence scores or graph nodes/edges JSON structure.
* **Expected Invariant:** Entity resolution raw PII is never exposed; matching operates on cryptographic hashes.
* **Possible Implementation Risks:** Deep graph traversal (`depth > 5`) causing memory allocation spikes.
* **Edge Cases:** Cyclic graph relationships, isolated orphan nodes.
* **Engineering Claim Being Made:** "Entity resolution uses privacy-preserving hashes and graph traversal is strictly bounded."
* **Appropriate Verification Methodology:** Graph structural invariant testing and memory allocation tracing.

---

### 2.9 Compliance, PSD2, & Real-Time Inference (`compliance.py`, `psd2.py`, `realtime_inference.py`)

* **Component:** `app.presentation.routers.compliance`, `app.presentation.routers.psd2`, `app.presentation.routers.realtime_inference`
* **Purpose:** Regulatory compliance reporting, PSD2 Strong Customer Authentication (SCA) evaluation, and streaming scoring.
* **Endpoint Specification:**
  * `GET /api/v1/compliance`
  * `GET /api/v1/psd2/sca`
  * `POST /api/v1/realtime/stream-score`
* **Request/Response Contract:**
  * *Response:* Compliance audit summaries, SCA exemption flags (`EXEMPT_TRA`, `MANDATE_SCA`), or streaming risk scores.
* **Expected Invariant:** PSD2 SCA evaluations deterministically assign exemption status based on transaction risk thresholds.
* **Possible Implementation Risks:** Inconsistent risk scoring between batch `/predict` and streaming `/realtime/stream-score`.
* **Edge Cases:** Amount exactly equal to TRA exemption boundary (€100, €250, €500).
* **Engineering Claim Being Made:** "PSD2 SCA exemption logic adheres strictly to RTS EBA risk threshold tables."
* **Appropriate Verification Methodology:** Boundary testing against EBA Regulatory Technical Standards (RTS) thresholds.

---

### 2.10 Real-Time WebSockets (`streaming_ws.py`, `training_ws.py`)

* **Component:** `app.presentation.websockets.streaming_ws`, `app.presentation.websockets.training_ws`
* **Purpose:** Bi-directional real-time event streaming and FL training progress push notifications.
* **Endpoint Specification:**
  * `GET /ws/stream`
  * `GET /ws/training`
* **Request/Response Contract:**
  * *Handshake:* WebSocket upgrade request with token authentication.
  * *Frames:* JSON event frames (`ALERT_GENERATED`, `FL_ROUND_COMPLETE`).
* **Expected Invariant:** Unauthorized WebSocket connections are closed immediately during handshake with code `1008`.
* **Possible Implementation Risks:** Unhandled client disconnects leading to dangling WebSocket connection references.
* **Edge Cases:** Client disconnection mid-frame broadcast, missing auth token in query string.
* **Engineering Claim Being Made:** "WebSocket connections enforce query-string token auth and handle abrupt disconnections gracefully."
* **Appropriate Verification Methodology:** WebSocket handshake auth testing and abrupt disconnect handling verification.

---

## 3. Comprehensive Verification Matrix

| Component | Target File | Verification Focus | Primary Methodology |
|---|---|---|---|
| **Global Error Handling** | `main.py` | RFC 7807 problem+json response format | Adversarial Exception Injection |
| **Content-Type Filter** | `main.py` | HTTP 415 rejection on non-JSON bodies | Media Type Fuzzing |
| **Trace Context** | `main.py` | W3C `traceparent` header injection | Header Assertion |
| **mTLS Verification** | `main.py` | Certificate fingerprint & CRL validation | Security Certificate Audit |
| **DDoS Throttling** | `main.py` | L7 sliding-window rate limiting | High-Throughput Burst Test |
| **Readiness Probe** | `health.py` | HTTP 503 on Redis/DB failure | Dependency Fault Injection |
| **ML Inference** | `predict.py` | Non-blocking `asyncio.to_thread` execution | Async Event Loop Concurrency |
| **Alert Enums** | `alerts.py` | HTTP 422 on invalid Enum params | Invalid Query Injection |
| **Case Idempotency** | `cases.py` | `Idempotency-Key` 24h deduplication | Concurrent Replay Testing |
| **ABAC & Audit** | `security.py` | Cross-tenant isolation & SHA-256 chain | Property-Based Invariant Test |
| **Gateway Rate Limit** | `gateway.py` | RFC `X-RateLimit-*` headers | Rate Limit Bucket Verification |
| **PSD2 SCA** | `psd2.py` | EBA RTS threshold compliance | Boundary Value Analysis |
| **WebSockets** | `streaming_ws.py` | Token handshake & abrupt disconnect | WS Handshake Security Audit |

---

*This inventory serves as the foundational specification reference for all subsequent empirical testing and verification suites.*
