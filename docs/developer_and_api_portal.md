# 📖 Developer & Interactive API Portal Specification

## 1. Overview & Architectural Role

The **Collaborative Fraud Intelligence (CF-Intelligence)** platform provides high-throughput gRPC communication channels for local bank training daemons, alongside an enterprise-grade REST & WebSocket API surface for real-time scoring, fuzzy entity matching, compliance export, and infrastructure diagnostics.

To accelerate bank consortium onboarding and SIEM/Core Banking integration, the platform features a dedicated **Developer Portal**, automated **Multi-Language SDK Code Generator**, a dark-themed **Scalar API Gateway**, and a **Live Interactive Request Runner Sandbox**.

---

## 2. API Surface & Gateway Index

| Endpoint / Gateway | Protocol | Authentication | Description |
| :--- | :---: | :---: | :--- |
| `GET /scalar` | HTTP/1.1 | Public / SSO | Interactive dark-themed Scalar API Reference targeting `/openapi.json` |
| `GET /openapi.json` | HTTP/1.1 | Public | OpenAPI 3.1.0 compliant JSON schema specification |
| `GET /docs` & `GET /redoc` | HTTP/1.1 | Public | Standard Swagger UI and ReDoc documentation gateways |
| `WS /ws/telemetry` | WebSocket / WSS | Bearer JWT | Real-time bi-directional telemetry: live transactions, alerts & training |
| `POST /api/v1/score-transaction` | HTTP/1.1 | Bearer JWT / API Key | Real-time payment fraud inference (<15ms) with SHAP attribution |
| `POST /api/v1/predict` | HTTP/1.1 | Bearer JWT / API Key | Full 9-signal composite risk inference & dynamic policy rules |
| `POST /api/v1/psi/match` | HTTP/1.1 | mTLS + Bearer JWT | Fuzzy MinHash LSH Private Set Intersection cross-bank matching |
| `POST /api/v1/coordinator/negotiate` | HTTP/1.1 | mTLS + Bearer JWT | Dynamic hardware & Non-IID Dirichlet hyperparameter negotiation |
| `POST /api/v1/cases/export/fincen-xml`| HTTP/1.1 | Bearer JWT (4-Eyes) | FinCEN BSA SAR XML compilation & supervisor cryptographic signing |
| `GET /api/v1/diagnostics/connectors` | HTTP/1.1 | Bearer JWT / Admin | Probe health across Kafka, Vault, KMS, Splunk, Redis & PostgreSQL |
| `POST /api/v1/diagnostics/test-connector` | HTTP/1.1 | Bearer JWT / Admin | On-demand live handshake ping test for specific enterprise adapter |

---

## 3. Multi-Language SDK Code Generation

The in-app Developer Portal (`/developer`, `/api-docs`) dynamically compiles clean, production-ready integration snippets for five core programming languages:

### 3.1 cURL CLI
```bash
curl -X POST https://cf-intelligence.vercel.app/api/v1/predict/score \
  -H "Authorization: Bearer $CFI_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "transaction_id": "txn_live_994821",
    "amount": 250000.0,
    "currency": "EUR",
    "sender_bank": "bank_alpha",
    "receiver_bank": "bank_beta",
    "features": [12.5, 0.85, 4.0, 14.0]
  }'
```

### 3.2 Python (httpx / AsyncIO)
```python
import httpx
import asyncio

async def score_transaction(api_key: str, payload: dict):
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(
            "https://cf-intelligence.vercel.app/api/v1/predict/score",
            headers=headers,
            json=payload
        )
        resp.raise_for_status()
        return resp.json()

if __name__ == "__main__":
    result = asyncio.run(score_transaction("cfi_key_99", {"transaction_id": "txn_994821", "amount": 250000.0}))
    print("Decision:", result.get("decision"), "Risk Score:", result.get("risk_score"))
```

### 3.3 Node.js / TypeScript (axios)
```typescript
import axios from 'axios';

interface RiskResponse {
  risk_score: number;
  decision: 'APPROVE' | 'CHALLENGE' | 'BLOCK_AND_ESCALATE';
  latency_ms: number;
}

export async function checkFraudRisk(apiKey: string, transaction: Record<string, unknown>): Promise<RiskResponse> {
  const { data } = await axios.post<RiskResponse>(
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

    public static String scoreTransaction(String apiKey, String jsonBody) throws IOException {
        MediaType JSON = MediaType.get("application/json; charset=utf-8");
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

---

## 5. Enterprise Connector Diagnostics Architecture

The `connector_diagnostics_service.py` evaluates live reachability and TLS/token validation across 7 enterprise banking infrastructure tiers:

1. **Apache Kafka:** Topic metadata fetch and broker latency probe (`kafka.internal:9092`).
2. **HashiCorp Vault:** Root PKI token check and transit mount inspection (`https://vault.internal:8200`).
3. **AWS KMS / HSM:** Hardware security module envelope encryption key status (`kms.eu-central-1.amazonaws.com`).
4. **Splunk HEC SIEM:** Raw event ingestion endpoint handshake (`https://splunk.internal:8088/services/collector`).
5. **Redis Sentinel / Cluster:** Master/Replica ping and atomic lock verification (`redis.internal:6379`).
6. **PostgreSQL Relational DB:** Connection pool health and multi-tenant schema isolation audit (`postgresql.internal:5432`).
7. **ISO 20022 Engine:** High-throughput `pacs.008` and `camt.053` XML parser benchmark.
