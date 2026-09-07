# 🔄 Protocol Versioning & Client Compatibility Matrix Specification

This document defines the semantic versioning scheme, gRPC header handshake protocol, REST/WebSocket schema versioning, and backward compatibility invariants for the Collaborative Fraud Intelligence (CFI) platform.

---

## 📌 Protocol Versioning Scheme

Protocol releases strictly adhere to **Semantic Versioning (SemVer 2.0.0)** (`MAJOR.MINOR.PATCH`):

- **MAJOR (`X.0.0`)**: Breaking changes to protobuf wire formats (`fl_service.proto`), required parameter serialization schemas, or cryptographic primitives (e.g. `v1.x` to `v2.x`). Requires client SDK upgrades.
- **MINOR (`x.Y.0`)**: Backward-compatible feature additions (e.g., new optional telemetry fields, updated drift metrics, additive database columns).
- **PATCH (`x.y.Z`)**: Backward-compatible bug fixes, internal algorithmic optimizations, and performance enhancements.

---

## 📑 Platform Compatibility Matrix

| Platform Version | gRPC Wire Protocol | Supported Client SDK Range | Schema Digest (SHA-256) | Lifecycle Status | Deprecation Date |
| :--- | :---: | :---: | :---: | :---: | :---: |
| **v1.0.0** | `1.0.0` | `1.0.0 - 1.99.99` | `a1b2c3d4e5f60718...` | ⚠️ Deprecated | 2026-10-01 |
| **v1.1.0** | `1.1.0` | `1.0.0 - 1.99.99` | `e5f6g7h8i9j01234...` | ⚠️ Maintenance | 2026-12-31 |
| **v2.0.0** | `2.0.0` | `2.0.0 - 2.99.99` | `9988776655443322...` | ✅ **Active Production** | N/A |
| **v2.1.0** | `2.1.0` | `2.0.0 - 2.99.99` | `3344556677889900...` | ✅ **Active Production** | N/A |
| **v3.0.0 (Roadmap)**| `3.0.0` | `3.0.0 - 3.99.99` | *(Planned)* | 🔬 Planned (PQC / zk-SNARK) | 2027-Q2 |

---

## 🤝 gRPC Header Handshake & Context Metadata

Every gRPC streaming request (`RegisterClient`, `Heartbeat`, `StreamModelParameters`) must transmit protocol metadata in the request context:

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

### Protocol Rejection Status Codes
If a bank node attempts to communicate with an incompatible protocol version:
- **`grpc.StatusCode.OUT_OF_RANGE`**: Client version is below the minimum supported version for the active consortium.
- **`grpc.StatusCode.UNIMPLEMENTED`**: Client major version does not match the server major release.
- **`grpc.StatusCode.FAILED_PRECONDITION`**: Feature schema hash mismatch indicates divergent local feature engineering pipelines.

---

## 🌐 HTTP REST & WebSocket Versioning Invariants

1. **Path-Based Prefix Routing**:
   - Production REST endpoints are namespaced under `/api/v1` or `/v1` (e.g. `/api/v1/score-transaction`, `/v1/webhooks/subscriptions`).
2. **Deprecation Signaling**:
   - During rolling upgrades and the 48-hour compatibility window, responses from legacy tiers include:
     ```http
     x-cfi-deprecation-warning: Version v2.0.0 will be retired on 2026-10-01.
     x-cfi-target-version: v2.1.0
     ```
3. **Additive JSON Contracts**:
   - Pydantic models in `backend/app/presentation/schemas/` enforce additive field updates with default fallbacks, preventing deserialization failures in older client libraries.
