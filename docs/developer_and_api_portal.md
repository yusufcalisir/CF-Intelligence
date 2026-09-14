# 📖 Developer & Interactive API Portal Specification

## 1. Overview & Architectural Role

The **Collaborative Fraud Intelligence (CF-Intelligence)** platform provides high-throughput gRPC and REST communication channels alongside an enterprise-grade WebSocket surface for real-time fraud scoring, fuzzy entity matching, compliance filing export, and infrastructure diagnostics.

To accelerate bank consortium onboarding, core banking connectivity, and SIEM integration, the platform features a dedicated **Developer Portal** (`/developer`), automated **Multi-Language SDK Code Generator**, a dark-themed **Scalar API Gateway** (`/scalar`), standard OpenAPI documentation gateways (`/docs`, `/redoc`), and a **Live Interactive Request Runner Sandbox**.

> [!NOTE]
> All endpoints enforce Zero Raw PII transmission. Identifiers are salted and hashed via HMAC-SHA256 before inference or cross-bank PSI operations.

---

## 2. API Surface & Gateway Index

| Endpoint / Gateway | Protocol | Authentication | Description |
| :--- | :---: | :---: | :--- |
| `GET /scalar` | HTTP/1.1 | Public / SSO | Interactive dark-themed Scalar API Reference targeting `/openapi.json` with 3-column layout |
| `GET /openapi.json` | HTTP/1.1 | Public | OpenAPI 3.1.0 compliant JSON schema specification (140+ active operations) |
| `GET /docs` & `GET /redoc` | HTTP/1.1 | Public | Interactive Swagger UI and ReDoc documentation gateways with CSP headers |
| `WS /ws/telemetry` | WebSocket / WSS | Bearer JWT | Real-time bi-directional telemetry: live scored transactions, critical fraud alerts & heartbeats |
| `WS /ws/training/{simulation_id}` | WebSocket / WSS | Bearer JWT | Redis pub/sub streaming of federated training round convergence events & per-bank AUC |
| `POST /api/v1/predict/score` | HTTP/1.1 | Bearer JWT / API Key | Real-time payment fraud inference (<10ms) with PyTorch GAT & SHAP attribution (alias `/api/v1/score-transaction`) |
| `POST /api/v1/predict` | HTTP/1.1 | Bearer JWT / API Key | Full 9-signal composite risk inference with dynamic policy rule evaluation |
| `POST /api/v1/predict/feedback` | HTTP/1.1 | Bearer JWT / API Key | Ground-truth fraud label ingestion into feedback loop with DP noise injection |
| `POST /api/v1/psi/match` | HTTP/1.1 | mTLS + Bearer JWT | Privacy-Preserving Diffie-Hellman Private Set Intersection (DH-PSI) cross-bank mule detection (alias `/api/v1/entities/psi-match`) |
| `POST /api/v1/entities/psi` | HTTP/1.1 | Bearer JWT | Cross-bank entity set intersection with fuzzy MinHash LSH matching |
| `POST /api/v1/entities/fuzzy-resolve` | HTTP/1.1 | Bearer JWT | Resolve fuzzy entity names against consortium identity graph via MinHash LSH |
| `POST /api/v1/entities/hmac-tokenize` | HTTP/1.1 | Bearer JWT | Tokenize raw identifiers into tenant-salted HMAC tokens enforcing Zero Raw PII |
| `GET\|POST /api/v1/coordinator/negotiate` | HTTP/1.1 | mTLS + Bearer JWT | Dynamic hardware (GPU VRAM, bandwidth) & Non-IID Dirichlet hyperparameter negotiation |
| `GET /api/v1/coordinator/clients` | HTTP/1.1 | Bearer JWT | Registered bank node runtime profiles, hardware status, and heartbeat timestamps |
| `POST /api/v1/coordinator/handshake` | HTTP/1.1 | mTLS + Bearer JWT | Bank client registration and hardware capability exchange |
| `POST /api/v1/cases/export/fincen-xml`| HTTP/1.1 | Bearer JWT (4-Eyes) | FinCEN BSA Electronic Filing SAR XML compilation with Four-Eyes dual supervisor signing |
| `GET /api/v1/banks/scoring-volume` | HTTP/1.1 | Bearer JWT / API Key | 24-hour aggregated transaction scoring volume and metrics across consortium banks |
| `GET /api/v1/training/{simulation_id}/rounds` | HTTP/1.1 | Bearer JWT | Training round convergence metrics: global AUC, round duration, per-bank AUC & loss |
| `GET /api/v1/diagnostics/connectors` | HTTP/1.1 | Bearer JWT / Admin | Probe health across 7 enterprise infrastructure tiers (Kafka, Vault, KMS, Splunk, Redis, DB, ISO 20022) |
| `POST /api/v1/diagnostics/test-connector` | HTTP/1.1 | Bearer JWT / Admin | On-demand live handshake ping test for specific enterprise adapter |
| `POST /api/v1/scenarios/inject-attack` | HTTP/1.1 | Bearer JWT | Inject Adversarial Byzantine Attack & Evaluate Multi-Krum Shield |
| `POST /api/v1/datasets/contract-audit` | HTTP/1.1 | Bearer JWT | Audit Ingested Transactions via Great Expectations Data Contract |
| `POST /api/v1/datasets/consortium-enroll` | HTTP/1.1 | Bearer JWT | Enroll Validated Partition into Federated Learning Round |

---

## 3. Multi-Language SDK Code Generation

The in-app Developer Portal (`/developer`) dynamically compiles production-ready integration snippets for five core programming languages:

### 3.1 cURL CLI
```bash
curl -X POST "https://cf-intelligence.vercel.app/api/v1/predict/score" \
  -H "Authorization: Bearer $CFI_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "transaction_id": "txn_live_994821",
    "account_id": "acc_eur_994821",
    "amount": 250000.0,
    "currency": "EUR",
    "merchant_id": "crypto_exchange_berlin",
    "country": "DE",
    "device_id": "device_fp_alpha_091"
  }'
```

### 3.2 Python (httpx / AsyncIO)
```python
import httpx
import asyncio

async def score_transaction(api_key: str, payload: dict) -> dict:
    url = "https://cf-intelligence.vercel.app/api/v1/predict/score"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(url, headers=headers, json=payload)
        resp.raise_for_status()
        return resp.json()

if __name__ == "__main__":
    payload = {
        "transaction_id": "txn_live_994821",
        "account_id": "acc_eur_994821",
        "amount": 250000.0,
        "currency": "EUR",
        "merchant_id": "crypto_exchange_berlin",
        "country": "DE",
        "device_id": "device_fp_alpha_091"
    }
    result = asyncio.run(score_transaction("cfi_live_key_alpha", payload))
    print(f"Decision: {result.get('decision')}, Risk Score: {result.get('risk_score')}, Latency: {result.get('latency_ms')}ms")
```

### 3.3 Node.js / TypeScript (axios)
```typescript
import axios from 'axios';

interface ScoreResponse {
  risk_score: number;
  risk_level: 'LOW' | 'MEDIUM' | 'HIGH' | 'CRITICAL';
  decision: 'APPROVE' | 'REVIEW' | 'BLOCK' | 'BLOCK_AND_ESCALATE';
  model_version: string;
  latency_ms: number;
}

export async function checkFraudRisk(apiKey: string, transaction: Record<string, unknown>): Promise<ScoreResponse> {
  const { data } = await axios.post<ScoreResponse>(
    'https://cf-intelligence.vercel.app/api/v1/predict/score',
    transaction,
    {
      headers: {
        Authorization: `Bearer ${apiKey}`,
        'Content-Type': 'application/json',
      },
      timeout: 5000,
    }
  );
  return data;
}
```

### 3.4 Java (OkHttp)
```java
import okhttp3.*;
import java.io.IOException;

public class FraudClient {
    private static final OkHttpClient client = new OkHttpClient();
    private static final MediaType JSON = MediaType.get("application/json; charset=utf-8");

    public static String scoreTransaction(String apiKey, String jsonBody) throws IOException {
        RequestBody body = RequestBody.create(jsonBody, JSON);
        Request request = new Request.Builder()
            .url("https://cf-intelligence.vercel.app/api/v1/predict/score")
            .header("Authorization", "Bearer " + apiKey)
            .post(body)
            .build();

        try (Response response = client.newCall(request).execute()) {
            if (!response.isSuccessful()) throw new IOException("Unexpected code " + response);
            return response.body().string();
        }
    }
}
```

### 3.5 Go (`net/http`)
```go
package main

import (
	"bytes"
	"fmt"
	"io"
	"net/http"
	"time"
)

func ScoreTransaction(apiKey string, jsonPayload []byte) ([]byte, error) {
	client := &http.Client{Timeout: 5 * time.Second}
	req, err := http.NewRequest("POST", "https://cf-intelligence.vercel.app/api/v1/predict/score", bytes.NewBuffer(jsonPayload))
	if err != nil {
		return nil, err
	}
	req.Header.Set("Authorization", "Bearer "+apiKey)
	req.Header.Set("Content-Type", "application/json")

	resp, err := client.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()

	return io.ReadAll(resp.Body)
}
```

---

## 4. Real-Time WebSocket Telemetry Protocol

### 4.1 Connection & Heartbeat Handshake
- **Connection URI:** `ws://<host>:<port>/ws/telemetry` or `wss://<host>:<port>/ws/telemetry`
- **Heartbeat Interval:** Server transmits `{"type": "PING", "timestamp": "..."}` every 30 seconds.
- **Client Acknowledgement:** Client responds with `{"action": "PONG"}` within 10 seconds to maintain channel liveness.

### 4.2 Subscription Frame
```json
{
  "action": "subscribe",
  "channels": ["transactions", "alerts", "heartbeat"]
}
```

### 4.3 High-Risk Alert Broadcast ($S \ge 700$)
```json
{
  "type": "FRAUD_ALERT",
  "transaction_id": "txn_live_994821",
  "bank_id": "bank_alpha",
  "amount": 250000.0,
  "currency": "EUR",
  "risk_score": 942,
  "decision": "BLOCK_AND_ESCALATE",
  "reason": "Velocity surge detected across 3 consortium nodes within 90 seconds",
  "timestamp": "2026-09-02T14:35:15Z"
}
```

### 4.4 Live Federated Training Round Streaming (`WS /ws/training/{simulation_id}`)

Streams real-time convergence envelopes as each federated aggregation round completes via Redis pub/sub (`training:{simulation_id}`):

```json
{
  "round": 4,
  "metrics": {
    "round": 4,
    "global_auc": 0.887,
    "loss": 0.283,
    "duration_sec": 3.42,
    "participating_banks": 3,
    "bank_metrics": {
      "bank_alpha": {"auc": 0.892, "loss": 0.274, "samples": 12500},
      "bank_beta": {"auc": 0.879, "loss": 0.291, "samples": 9800},
      "bank_gamma": {"auc": 0.890, "loss": 0.284, "samples": 11200}
    }
  },
  "timestamp": 1725580800.0
}
```

---

## 5. Enterprise Connector Diagnostics Architecture

The `ConnectorDiagnosticsService` evaluates live reachability, TLS handshakes, and round-trip ping latency across 7 enterprise banking infrastructure tiers:

1. **Apache Kafka:** Topic metadata fetch and broker latency probe (`kafka.internal:9092`).
2. **HashiCorp Vault:** Root PKI token check and transit mount inspection (`https://vault.internal:8200`).
3. **AWS KMS / HSM:** Hardware security module envelope encryption key status (`kms.eu-central-1.amazonaws.com`).
4. **Splunk HEC SIEM:** Raw event ingestion endpoint handshake (`https://splunk.internal:8088/services/collector`).
5. **Redis Sentinel / Cluster:** Master/Replica ping and atomic lock verification (`redis.internal:6379`).
6. **PostgreSQL Relational DB:** Connection pool health and multi-tenant schema isolation audit (`postgresql.internal:5432`).
7. **ISO 20022 Engine:** High-throughput `pacs.008` and `camt.053` XML parser benchmark.

### 5.1 Diagnostics Overview Schema (`GET /api/v1/diagnostics/connectors`)
```json
{
  "total_connectors": 7,
  "healthy_connectors": 7,
  "avg_latency_ms": 2.4,
  "connectors": [
    {
      "connector_id": "kafka",
      "name": "Apache Kafka Event Broker",
      "status": "HEALTHY",
      "latency_ms": 3.4,
      "protocol": "PLAINTEXT/SASL_SSL",
      "endpoint": "kafka.internal:9092"
    },
    {
      "connector_id": "vault",
      "name": "HashiCorp Vault PKI Engine",
      "status": "HEALTHY",
      "latency_ms": 1.8,
      "protocol": "HTTPS",
      "endpoint": "https://vault.internal:8200"
    }
  ]
}
```

### 5.2 On-Demand Active Probe (`POST /api/v1/diagnostics/test-connector`)
```json
{
  "connector_id": "vault",
  "success": true,
  "status_code": 200,
  "round_trip_ms": 2.1,
  "handshake_summary": "TLS 1.3 handshake verified; Vault transit engine responsive.",
  "diagnostics_log": [
    "Resolving vault.internal:8200 DNS record",
    "TLS 1.3 mutual handshake established",
    "Transit secret engine mount status verified: OK"
  ]
}
```

---

## 6. In-App Developer Portal Architecture (`/developer`)

The Developer Portal UI ([`frontend/src/pages/ApiDocsPage.tsx`](../frontend/src/pages/ApiDocsPage.tsx)) provides a rich client-side workspace:

- **Top Action Bar:**
  - **Export OpenAPI JSON:** Generates and downloads a client-side OpenAPI 3.1.0 specification bundle (`cfi-openapi-spec.json`).
  - **Scalar Gateway Link (`/scalar`):** Opens the modern, dark-themed 3-column reference powered by `@scalar/api-reference`.
  - **ReDoc Link (`/redoc`):** Accesses ReDoc documentation with deep response schema exploration.
  - **Swagger UI Link (`/docs`):** Accesses standard OpenAPI interactive Swagger gateway.
- **Interactive Request Runner Sandbox:**
  - Enables developers to select any consortium endpoint, configure parameters or JSON payload, and execute live queries directly against the platform.
  - Measures execution latency (`performance.now()`), formats HTTP status badges (`200 OK`, `400 Bad Request`), and renders JSON syntax-highlighted responses.

---

## 7. 🧪 Automated Test Suite Parity

The Developer Portal and API surface are thoroughly validated across both frontend and backend test suites:

### 7.1 Frontend Vitest Suite
```bash
npm --prefix frontend test src/pages/__tests__/ApiDocsPage.test.tsx
# 3 passed (100% Pass)
```
- Validates portal header, quick links, and endpoints directory rendering.
- Verifies code generator language tabs switching (Python, Java, Node, cURL).
- Tests live interactive request runner execution and response payload display.

### 7.2 Backend Pytest Suites
```bash
python -m pytest \
  backend/tests/unit/test_developer_portal_api_parity.py \
  backend/tests/unit/test_connector_diagnostics.py \
  backend/tests/unit/test_openapi_contract_accuracy.py \
  backend/tests/contract/test_openapi_contract.py -v
# 22 passed in 10.26s (100% Pass)
```

| Test Suite | Test Count | Scope |
| :--- | :---: | :--- |
| `test_developer_portal_api_parity.py` | 5 | `/predict/score` alias, `/coordinator/negotiate` POST, `/psi/match` direct, training rounds, `/scalar` gateway |
| `test_connector_diagnostics.py` | 11 | Health status across all 7 infrastructure tiers, on-demand active probe tests, error handling |
| `test_openapi_contract_accuracy.py` | 2 | OpenAPI 3.1 schema completeness (140+ endpoints), FinCEN BSA XML export contract |
| `test_openapi_contract.py` | 4 | Core OpenAPI structure, required endpoints presence, HTTP method status codes, component schemas |
