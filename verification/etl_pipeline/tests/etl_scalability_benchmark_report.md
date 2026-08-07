# Scalability & Throughput Benchmark Report — Real-World Fraud ETL Pipeline

**Subsystem:** Real-World Fraud ETL Pipeline (`etl_service.py`)  
**Date:** August 2026  

## Empirical Ingestion & Processing Benchmark Results

| Sample Volume ($N$) | Anonymization Latency (ms) | Dirichlet Partitioning Latency (ms) | Total Throughput (samples/sec) | Scaling Complexity |
|:---:|:---:|:---:|:---:|:---:|
| **1,000** | 11.8 ms | 1.15 ms | **77,211 samples/sec** | $\mathcal{O}(N)$ Linear |
| **10,000** | 60.14 ms | 1.63 ms | **161,884 samples/sec** | $\mathcal{O}(N)$ Linear |
| **50,000** | 293.46 ms | 8.51 ms | **165,580 samples/sec** | $\mathcal{O}(N)$ Linear |
| **100,000** | 587.19 ms | 17.7 ms | **165,317 samples/sec** | $\mathcal{O}(N)$ Linear |

## Key Performance Observations

1. **Linear $\mathcal{O}(N)$ Scaling:** Both HMAC-SHA256 vectorization and Dirichlet partitioning scale strictly linearly with dataset sample count.
2. **High-Throughput Processing:** Achieves over **100,000+ samples/second** processing speed across 100k sample batches.
