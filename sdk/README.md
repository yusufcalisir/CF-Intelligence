# Enterprise Bank Connector SDK (`cfi-connector-sdk`)

The **Collaborative Fraud Intelligence (CFI) Connector SDK** provides standardized, versioned interfaces for external banking IT engineering teams to connect heterogeneous core banking transaction systems (Oracle Flexcube, Temenos Transact, Thought Machine Vault, Finacle) to the privacy-preserving consortium network.

---

## 1. Directory Structure

```text
sdk/
├── README.md                          # Top-level SDK overview & quick-start guide
├── examples/
│   └── reference_bank_connector.py    # Complete reference implementation of a bank connector
└── python/                            # Python 3.10+ Integration SDK package
    ├── pyproject.toml                 # Package build definition (pip install cfi-connector-sdk)
    ├── README.md                      # Package distribution notes
    ├── cfi_connector_sdk/             # Core library source code
    │   ├── adapters/
    │   │   ├── transaction_adapter.py # BaseTransactionAdapter & NormalizedTransaction schema
    │   │   ├── feature_adapter.py     # BaseFeatureAdapter (Rolling velocity & ratio extraction)
    │   │   └── entity_adapter.py      # BaseEntityAdapter (Type-Salted HMAC-SHA256 zero-PII masking)
    │   ├── client/
    │   │   └── local_fl_client.py     # LocalFLClient (mTLS connection, gradient submission)
    │   └── health.py                  # ConnectorHealthMonitor & operational readiness probes
    └── tests/                         # Automated SDK test suite (11 Tests)
        ├── test_adapters.py           # Unit tests for transaction, feature & entity adapters
        ├── test_client.py             # Unit tests for LocalFLClient & gradient submission
        └── test_health.py             # Unit tests for health monitor probes
```

---

## 2. Core Capabilities & Invariants

| Component | Responsibility | Privacy & Security Invariant |
|:---|:---|:---|
| **`BaseTransactionAdapter`** | Normalizes heterogeneous payment records (ISO 20022 `pacs.008`, JSON, XML) to `NormalizedTransaction`. | Strict field validation & currency normalization. |
| **`BaseFeatureAdapter`** | Computes rolling velocity counters (`tx_count_1h`, `tx_count_24h`, `amount_sum_24h`). | Local computation only; raw transaction data is never exposed. |
| **`BaseEntityAdapter`** | Masks account IDs, IBANs, and national IDs via HMAC-SHA256 using bank-specific salt. | **Zero Raw PII**: Irreversible 64-character hex digest. |
| **`LocalFLClient`** | Communicates with the consortium FL coordinator via gRPC mTLS. | HSM payload signing, zlib compression, and DP gradient submission. |
| **`ConnectorHealthMonitor`**| Probes message broker connectivity (AMQP/Kafka) and X.509 certificate expiry. | Real-time health reporting (`HEALTHY`, `DEGRADED`, `UNHEALTHY`). |

---

## 3. Installation & Quick Start

```bash
# Install local development package
pip install -e sdk/python

# Run reference bank connector example
python sdk/examples/reference_bank_connector.py --bank-id bank-a --salt sec_bank_a_salt
```

---

## 4. Automated Testing

Verify the SDK implementation using pytest:

```bash
pytest sdk/python/tests/ -v
# Result: 11/11 PASSED in <1s
```

---

## 5. Technical Documentation

For an in-depth integration guide with core banking examples, see:
- [`docs/connector_sdk_guide.md`](../docs/connector_sdk_guide.md)
