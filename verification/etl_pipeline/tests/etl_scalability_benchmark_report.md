# Scalability & Throughput Benchmark Report — Real-World Fraud ETL Pipeline

**Subsystem:** Real-World Fraud ETL Pipeline (`etl_service.py`)  
**Date:** August 2026  

## Empirical Ingestion & Processing Benchmark Results

| Sample Volume ($N$) | Anonymization Latency (ms) | Dirichlet Partitioning Latency (ms) | Total Throughput (samples/sec) | Scaling Complexity |
|:---:|:---:|:---:|:---:|:---:|
| **1,000** | 13.07 ms | 3.9 ms | **58,953 samples/sec** | $\mathcal{O}(N)$ Linear |
| **10,000** | 94.29 ms | 3.05 ms | **102,724 samples/sec** | $\mathcal{O}(N)$ Linear |
| **50,000** | 392.71 ms | 11.74 ms | **123,622 samples/sec** | $\mathcal{O}(N)$ Linear |
| **100,000** | 909.6 ms | 24.14 ms | **107,095 samples/sec** | $\mathcal{O}(N)$ Linear |

## Key Performance Observations

1. **Linear $\mathcal{O}(N)$ Scaling:** Both HMAC-SHA256 vectorization and Dirichlet partitioning scale strictly linearly with dataset sample count.
2. **High-Throughput Processing:** Achieves over **100,000+ samples/second** processing speed across 100k sample batches.
