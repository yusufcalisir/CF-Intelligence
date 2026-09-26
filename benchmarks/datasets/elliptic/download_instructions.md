# Elliptic Bitcoin Transaction Graph Benchmark Dataset

## Dataset Overview
- **Source**: Elliptic / Weber et al. (NeurIPS 2019)
- **Reference**: Weber, M., et al. (2019). *Anti-Money Laundering in Bitcoin: Experimenting with Graph Convolutional Networks for Financial Forensics*.
- **Scale**: 203,769 node transactions across 49 distinct timesteps (each ~2 weeks); 234,355 directed edges.
- **Classes**: Illicit (class 1, ~4,545), Licit (class 2, ~42,019), and Unknown (class 3, ~157,205).
- **Features**: 166 features per node (94 local transaction attributes, 72 aggregated 1-hop neighbor attributes).

## Acquisition Instructions
To download the Elliptic dataset from Kaggle:
```bash
kaggle datasets download -d ellipticco/elliptic-data-set -p benchmarks/datasets/elliptic/ --unzip
```
Expected uncompressed files:
- `elliptic_txs_features.csv`
- `elliptic_txs_classes.csv`
- `elliptic_txs_edgelist.csv`

## Critical Scientific Invariant: Zero Temporal Data Leakage
Standard cross-validation randomly splitting nodes across the entire timeline creates severe **future-to-past data leakage**. 
The official scientific split must be preserved:
- **Training Graph**: Timesteps $t \in [1, 34]$ (138,568 nodes).
- **Testing Graph**: Timesteps $t \in [35, 49]$ (65,201 nodes).
- **Edge Traversal**: During inference on test timestep $t_{\mathrm{eval}}$, edges pointing to future timesteps $t' > t_{\mathrm{eval}}$ are strictly masked.
