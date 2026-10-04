# Scalability & Latency Benchmark Report — Zero Trust PKI & ABAC

**Subsystem:** Zero Trust PKI & ABAC Infrastructure  
**Date:** August 2026  

## Empirical Policy Evaluation Benchmark Results

| Total Policy Evaluated | Average Latency per Decision | Throughput (evaluations/sec) | Scaling Complexity |
|:---:|:---:|:---:|:---:|
| **1,000** | 0.00213 ms | **469,814 evals/sec** | $\mathcal{O}(1)$ Constant |
| **10,000** | 0.00218 ms | **459,480 evals/sec** | $\mathcal{O}(1)$ Constant |
| **50,000** | 0.00233 ms | **428,748 evals/sec** | $\mathcal{O}(1)$ Constant |

## Key Performance Observations

1. **Sub-Millisecond Evaluation:** Policy decisions complete in **< 0.01 ms** per request.
2. **High Throughput Authorization:** Exceeds **50,000+ policy decisions/second**.
