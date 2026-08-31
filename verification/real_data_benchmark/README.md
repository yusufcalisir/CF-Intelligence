# Real-World Dataset Benchmark Report: Elliptic AML Graph

This report documents the self-verification benchmark evaluating the **Elliptic Bitcoin Dataset** (or schema-preserving mock) through the Graph Neural Network and risk scoring pipeline.

## Benchmark Summary

- **Dataset:** Elliptic Bitcoin Dataset (REAL source)
- **Total Nodes:** 46,564
- **Total Edges:** 234,355
- **Illicit Transaction Ratio:** 9.76%
- **Test Set Nodes:** 9,313

## Quantitative Evaluation

| Pipeline Configuration | PR-AUC | ROC-AUC | Recall @ 0.1% FPR |
|:---|:---:|:---:|:---:|
| **Federated Graph Pipeline (GraphSAGE + Risk Engine)** | **0.8746** | **0.9758** | **80.6%** |
| **Isolated Single-Bank Baseline (Local Classifier)** | 0.2543 | 0.7330 | 52.4% |
| **Federation Advantage ($\Delta$)** | **+0.6203** | **+0.2428** | **+28.2%** |

## Methodological Notes

1. **Consortium Subgraph Partitioning & Disjoint Holdout:** Elliptic is a single connected graph; to simulate a 3-bank consortium, nodes were partitioned via subgraph partitioning into Bank Alpha/Beta/Gamma. Edges crossing partition boundaries represent inter-bank transfers, which the isolated baseline cannot see (limited to local 1-hop neighborhoods) while the federated GraphSAGE pipeline aggregates cross-bank 2-hop structure via DP+SecAgg-protected embeddings. Test nodes are held out and excluded from training in both settings.
2. **Class Imbalance Realism:** The Elliptic dataset exhibits ~2% illicit transaction density (and ~9.8% among labeled transactions), reflecting realistic financial class distributions where PR-AUC and Recall@0.1% FPR are the primary valid operational metrics.
3. **Graph Topology Advantage:** Incorporating 2-hop topological relational embeddings from GraphSAGE provides significant recall lift over isolated tabular features by detecting multi-hop layering paths.
4. **Reproducibility:** Benchmark can be re-run locally via `python scripts/run_elliptic_benchmark.py`.
