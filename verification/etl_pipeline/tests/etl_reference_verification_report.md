# Pure-Python Reference Verification Report — Real-World Fraud ETL Pipeline

**Subsystem:** Real-World Fraud ETL Pipeline (`etl_service.py`)  
**Date:** August 2026  
**Total Scenarios Evaluated:** 25  
**Passed Scenarios:** 25 / 25 (**100%**)  

## Mathematical & Statistical Invariants Verified

1. **Exact Sample Conservation:** Sum of partition samples equals total samples across all Dirichlet split configurations.
2. **Disjoint Client Index Invariant:** Union of bank index sets equals full dataset indices and sets are mutually disjoint.
3. **Deterministic HMAC-SHA256 PII Hashing:** 64 hex characters, non-reversible, zero collision on distinct inputs.
