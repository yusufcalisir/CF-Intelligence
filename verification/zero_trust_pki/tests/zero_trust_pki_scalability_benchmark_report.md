# Scalability & Latency Benchmark Report — Zero Trust PKI & ABAC

**Subsystem:** Zero Trust PKI & ABAC Infrastructure  
**Date:** August 2026  

## Empirical Policy Evaluation Benchmark Results

| Total Policy Evaluated | Average Latency per Decision | Throughput (evaluations/sec) | Scaling Complexity |
|:---:|:---:|:---:|:---:|
| **1,000** | 0.00222 ms | **451,385 evals/sec** | $\mathcal{O}(1)$ Constant |
| **10,000** | 0.00238 ms | **420,985 evals/sec** | $\mathcal{O}(1)$ Constant |
| **50,000** | 0.00244 ms | **410,263 evals/sec** | $\mathcal{O}(1)$ Constant |

## Key Performance Observations

1. **Sub-Millisecond Evaluation:** Policy decisions complete in **< 0.01 ms** per request.
2. **High Throughput Authorization:** Exceeds **50,000+ policy decisions/second**.
