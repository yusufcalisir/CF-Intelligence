# GraphSAGE Algorithm Specification

## 1. Problem Solved
Traditional graph neural networks (e.g., GCN) are transductive: they require the full graph Laplacian and cannot generate embeddings for nodes unseen during training.

In retail banking, thousands of new accounts, cards, and counterparty merchants are created daily. GraphSAGE (Sample and Aggregate; Hamilton et al., 2017) provides **inductive node representation learning**, enabling instantaneous feature generation for newly observed nodes by learning aggregator functions over local graph neighborhoods.

---

## 2. Implementation in CF-Intelligence
- **Location**: [`backend/app/application/services/graph_embedding_model.py`](file:///backend/app/application/services/graph_embedding_model.py)
- **Model Topology**: 2-Layer Inductive Graph Neural Network.
- **Neighborhood Aggregation**:
  At search depth $k \in \{1, 2\}$, for node $v \in \mathcal{V}$:

  $$h_{\mathcal{N}(v)}^{(k)} = \mathrm{AGGREGATE}_k\left( \left\{ h_u^{(k-1)}, \, \forall u \in \mathcal{N}(v) \right\} \right)$$

  $$h_v^{(k)} = \sigma\left( \mathbf{W}^{(k)} \cdot \left[ h_v^{(k-1)} \,\|\, h_{\mathcal{N}(v)}^{(k)} \right] \right)$$

  $$z_v = \frac{h_v^{(K)}}{\|h_v^{(K)}\|_2}$$

  where:
  - $\mathrm{AGGREGATE}_k$ is the Mean Aggregator: $\frac{1}{|\mathcal{N}(v)|} \sum_{u \in \mathcal{N}(v)} h_u^{(k-1)}$.
  - $\mathbf{W}^{(k)}$ are learnable weight matrices (Layer 1: $166 \to 128$, Layer 2: $128 \to 64$).
  - $[\cdot \,\|\, \cdot]$ denotes vector concatenation.
  - $\sigma$ is non-linear activation (ReLU).
  - $z_v$ is the final $L_2$-normalized 64-dimensional inductive node embedding.

---

## 3. Threat Model & Security Assumptions
- **Graph Boundary Scoping**: In cross-bank deployments, Bank Alpha never traverses raw edges into Bank Beta's proprietary network.
- **Privacy Boundary**: Cross-bank graph links are constructed strictly via anonymized entity hashes derived through MinHash LSH Private Set Intersection.

---

## 4. Operational Limitations
- **Hub Node Scalability**: Nodes with thousands of incident transaction edges (e.g., high-volume payment processors or crypto exchanges) require neighborhood sampling ($S_1 = 25, S_2 = 10$) to prevent memory explosion.
- **Temporal Leakage**: When evaluated on transaction graphs (e.g., Elliptic), edge traversals must strictly enforce temporal causality ($t_{\mathrm{edge}} \le t_{\mathrm{query}}$) to prevent future-lookahead data leakage.

---

## 5. Test Suite Verification
- **Unit Tests**: [`backend/tests/unit/test_graph_embedding.py`](file:///backend/tests/unit/test_graph_embedding.py)
- **Benchmark Runner**: [`benchmarks/runners/run_graph_benchmark.py`](file:///benchmarks/runners/run_graph_benchmark.py)
- **Benchmark Artifact**: [`benchmarks/results/raw/graphsage_elliptic_benchmark.json`](file:///benchmarks/results/raw/graphsage_elliptic_benchmark.json)
