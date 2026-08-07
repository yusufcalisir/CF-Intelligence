# CFI Connector SDK (`cfi-connector-sdk`)

Standardized Bank Connector SDK for the **Collaborative Fraud Intelligence (CFI) Network**.

## Installation

```bash
pip install cfi-connector-sdk
```

For local development and testing:
```bash
pip install -e .[dev]
```

## Features & Adapters

- **`BaseTransactionAdapter`**: Standardized payment schema normalization (`NormalizedTransaction`).
- **`BaseFeatureAdapter`**: Rolling velocity feature calculations (1h count, 24h count, 24h sum).
- **`BaseEntityAdapter`**: Privacy-preserving HMAC-SHA256 customer identifier resolution and payload masking.
- **`LocalFLClient`**: gRPC mTLS communication, HSM payload signing, zlib compression, and DP gradient submission.
- **`ConnectorHealthMonitor`**: Operational health probes for message brokers and X.509 certificate validity.

## Running Tests

```bash
cd sdk/python
pytest -v
```

## Packaging & Building

```bash
python -m build
twine check dist/*
```
