# API High-Throughput Performance & Concurrency Benchmark Report

## Executive Summary

This report documents empirical performance, latency percentiles, memory allocations, SerDe overhead, and concurrency scaling metrics for the API subsystem.

---

## 1. Endpoint Latency Percentiles

| Endpoint | p50 (Median) | p95 Latency | p99 Latency | Mean Latency |
|---|---|---|---|---|
| `GET /health` | 12.9 ms | 43.72 ms | 43.72 ms | 15.01 ms |
| `POST /api/v1/predict (Standard)` | 2759.44 ms | 8318.8 ms | 8318.8 ms | 3128.36 ms |
| `GET /api/v1/alerts` | 13.85 ms | 20.7 ms | 20.7 ms | 14.37 ms |
| `POST /api/v1/cases` | 20.72 ms | 2710.4 ms | 2710.4 ms | 200.31 ms |
| `POST /api/v1/security/abac/evaluate` | 21.64 ms | 34.7 ms | 34.7 ms | 22.94 ms |

---

## 2. Concurrency Scaling & Throughput (RPS)

| Worker Threads | Measured Throughput (RPS) | Scaling Characteristics |
|---|---|---|
| 1 Threads | 0.37 RPS | Threadpool offloaded `asyncio.to_thread` execution |
| 5 Threads | 1.77 RPS | Threadpool offloaded `asyncio.to_thread` execution |
| 10 Threads | 2.55 RPS | Threadpool offloaded `asyncio.to_thread` execution |
| 20 Threads | 4.92 RPS | Threadpool offloaded `asyncio.to_thread` execution |

---

## 3. Serialization, Memory & Payload Scaling

- **Per-Request JSON SerDe Overhead:** `0.0127 ms`
- **Peak Memory Allocation (`tracemalloc`):** `1.26 MB`
- **Small Payload (100B) Latency:** `2717.77 ms`
- **Large Payload (10KB) Latency:** `2732.83 ms`

---

## 4. Theoretical vs. Observed Complexity Analysis

| Endpoint / Logic | Theoretical Time Complexity | Theoretical Space Complexity | Empirical Bottleneck Analysis |
|---|---|---|---|
| `GET /health` | $\mathcal{O}(1)$ | $\mathcal{O}(1)$ | Memory lookup only; ultra-fast (<15ms). |
| `POST /api/v1/predict` | $\mathcal{O}(F + M)$ | $\mathcal{O}(F)$ | PyTorch forward pass $\mathcal{O}(F)$ offloaded to threadpool workers; event loop stays non-blocking. |
| `GET /api/v1/alerts` | $\mathcal{O}(K)$ | $\mathcal{O}(K)$ | Query limit $K$ bounded; response generation scales linearly with pagination size. |
| `POST /api/v1/cases` | $\mathcal{O}(1)$ | $\mathcal{O}(1)$ | Redis/In-memory key lookup $\mathcal{O}(1)$ for 24h idempotency deduplication. |

*Verified by Empirical Performance Benchmark Suite.*
