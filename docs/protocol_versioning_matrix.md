# 🔄 Protocol Versioning & Client Compatibility Matrix Specification

This document defines the semantic versioning scheme, gRPC header handshake protocol, REST/WebSocket schema versioning, and backward compatibility invariants for the Collaborative Fraud Intelligence (CFI) platform.

---

## 📌 1. Protocol Versioning Scheme

Protocol releases strictly adhere to **Semantic Versioning (SemVer 2.0.0)** (`MAJOR.MINOR.PATCH`):

- **MAJOR (`X.0.0`)**: Breaking changes to protobuf wire formats ([`fl_service.proto`](../backend/app/infrastructure/grpc/proto/fl_service.proto)), required parameter serialization schemas, or cryptographic primitives (e.g. `v1.x` to `v2.x`). Requires client SDK upgrades.
- **MINOR (`x.Y.0`)**: Backward-compatible feature additions (e.g., new optional telemetry fields, updated drift metrics, additive database columns).
- **PATCH (`x.y.Z`)**: Backward-compatible bug fixes, internal algorithmic optimizations, and performance enhancements.

---

## 📑 2. Platform Compatibility Matrix

The domain compatibility bounds are enforced by [`VersionCompatibilityMatrix`](../backend/app/domain/protocol_versioning.py):

| Platform Version | gRPC Wire Protocol | Supported Client SDK Range | Schema Digest (SHA-256) | Lifecycle Status | Deprecation Date |
| :--- | :---: | :---: | :---: | :---: | :---: |
| **v1.0.0** | `1.0.0` | `1.0.0 - 1.99.99` | `a1b2c3d4e5f60718...` | ⚠️ Deprecated | 2026-10-01 |
| **v1.1.0** | `1.1.0` | `1.0.0 - 1.99.99` | `e5f6g7h8i9j01234...` | ⚠️ Maintenance | 2026-12-31 |
| **v2.0.0** | `2.0.0` | `2.0.0 - 2.99.99` | `9988776655443322...` | ✅ **Active Production** | N/A |
| **v2.1.0** | `2.1.0` | `2.0.0 - 2.99.99` | `3344556677889900...` | ✅ **Active Production** | N/A |
| **v3.0.0 (Roadmap)**| `3.0.0` | `3.0.0 - 3.99.99` | *(Planned)* | 🔬 Planned (PQC / zk-SNARK) | 2027-Q2 |

---

## 🤝 3. gRPC Header Handshake & Context Metadata

Every gRPC streaming request (`RegisterClient`, `Heartbeat`, `StreamModelParameters`) is intercepted by [`ProtocolVersionInterceptor`](../backend/app/infrastructure/grpc/version_interceptor.py) to validate client protocol metadata:

```
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                              gRPC CLIENT HANDSHAKE METADATA                            │
├──────────────────────────┬──────────────────────────────────┬──────────────────────────┤
│ METADATA KEY             │ EXAMPLE VALUE                    │ VALIDATION PURPOSE       │
├──────────────────────────┼──────────────────────────────────┼──────────────────────────┤
│ `x-cfi-protocol-version` │ `2.1.0`                          │ SemVer compatibility     │
│ `x-cfi-schema-hash`      │ `e3b0c44298fc1c14...`            │ Feature schema alignment │
│ `x-cfi-tenant-id`        │ `bank_alpha`                     │ ContextVar routing       │
│ `x-cfi-mtls-fingerprint` │ `SHA256:7b908f24...`             │ X.509 cert binding       │
└──────────────────────────┴──────────────────────────────────┴──────────────────────────┘
```

### Protocol Rejection & Negotiation Semantics
The server evaluates `(client_version, client_schema_hash)` against the active matrix:
- **`VersionNegotiationStatus.COMPATIBLE`**: Client version satisfies SemVer major alignment and falls within `[min_supported_version, max_supported_version]`.
- **`VersionNegotiationStatus.DEGRADED_COMPATIBLE`**: Version matches, but client feature schema hash differs from the consortium registry (logged as a drift warning).
- **`VersionNegotiationStatus.INCOMPATIBLE`**: Aborts request with `grpc.StatusCode.FAILED_PRECONDITION` (or `OUT_OF_RANGE`), directing the client node to the consortium upgrade portal.

---

## 🌐 4. HTTP REST & WebSocket Versioning Invariants

1. **Path-Based Prefix Routing**:
   - Production REST endpoints are namespaced under `/api/v1` or `/v1` (e.g. `/api/v1/score-transaction`, `/api/v1/predict`, `/v1/webhooks/subscriptions`, `/v1/inference/score`).
2. **RFC 8594 Standard Deprecation & Sunset Headers**:
   - `APIVersionLifecycleMiddleware` in `backend/app/main.py` attaches standard version lifecycle headers to all HTTP responses:
     ```http
     X-API-Version: v1
     Deprecation: Sat, 01 Jan 2026 00:00:00 GMT
     Sunset: Sat, 01 Jul 2026 00:00:00 GMT
     ```
3. **Consortium Deprecation Warning Headers**:
   - During rolling upgrades and migration windows, legacy endpoints signal target versions:
     ```http
     x-cfi-deprecation-warning: Version v2.0.0 will be retired on 2026-10-01.
     x-cfi-target-version: v2.1.0
     ```
4. **Additive JSON Contracts**:
   - Pydantic models across `backend/app/presentation/routers/` enforce additive field updates with default values, preventing serialization crashes in older client libraries.

---

## 🧪 5. Automated Test Verification Matrix

Protocol negotiation, SemVer comparison, and lifecycle header adherence are continuously verified:

| Test Suite | File Path | Verified Capabilities | Status |
| :--- | :--- | :--- | :---: |
| **Protocol Versioning** | `backend/tests/unit/test_protocol_versioning.py` | SemVer parsing (`1.0.0 < 2.0.0`), matrix negotiation, gRPC metadata extraction | `3/3 PASSED` |
| **OpenAPI Contract Accuracy** | `backend/tests/unit/test_openapi_contract_accuracy.py` | Route schema accuracy, lifecycle headers, FinCEN export endpoints | `2/2 PASSED` |
