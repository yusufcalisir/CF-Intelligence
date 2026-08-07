# Scalability & Latency Benchmark Report — Zero Trust PKI & ABAC

**Subsystem:** Zero Trust PKI & ABAC Infrastructure  
**Date:** August 2026  

## Empirical Policy Evaluation Benchmark Results

| Total Policy Evaluated | Average Latency per Decision | Throughput (evaluations/sec) | Scaling Complexity |
|:---:|:---:|:---:|:---:|
| **1,000** | 0.00295 ms | **339,236 evals/sec** | $\mathcal{O}(1)$ Constant |
| **10,000** | 0.0029 ms | **344,932 evals/sec** | $\mathcal{O}(1)$ Constant |
| **50,000** | 0.00271 ms | **368,489 evals/sec** | $\mathcal{O}(1)$ Constant |

## Key Performance Observations

1. **Sub-Millisecond Evaluation:** Policy decisions complete in **< 0.01 ms** per request.
2. **High Throughput Authorization:** Exceeds **50,000+ policy decisions/second**.
