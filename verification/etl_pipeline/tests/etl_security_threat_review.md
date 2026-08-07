# Security & Threat Model Review — Real-World Fraud ETL Pipeline

**Subsystem:** Real-World Fraud ETL Pipeline (`etl_service.py`)  
**Date:** August 2026  

---

## Evaluated Threat Vectors

1. **PII Identity Leakage:** HMAC-SHA256 salt hashing prevents raw account numbers, IP addresses, and device IDs from persisting on disk. 🛡️ **MITIGATED**
2. **Cross-Bank Dictionary Attacks:** Per-node master salts prevent rainbow table lookup attacks. 🛡️ **MITIGATED**
3. **Data Truncation / Serialization Errors:** PyArrow schema validation and Snappy binary compression enforce strict column types. 🛡️ **MITIGATED**
