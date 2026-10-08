"""Unit tests for Elliptic Bitcoin Transaction Graph Loader (Phase 8, Sub-Plan 8.1).

Validates:
1. Real dataset ingestion from Parquet cache and CSV files.
2. Exact dataset scale (203,769 total nodes, 46,564 labeled nodes).
3. Exact feature dimensionality (166 dimensions: timestep + 165 features).
4. Strict temporal zero-leakage split (timesteps 1-34 train, 35-49 test).
5. Zero cross-temporal edge leakage between train and test partitions.
6. Node label mappings: y in {0, 1} (labeled only) and y in {-1, 0, 1} (include_unknown=True).
7. PyG-compatible edge_index tensor representation and NetworkX export.
8. Synthetic mock fallback behavior and schema parity.
"""

from __future__ import annotations

import numpy as np
import pytest
import torch

from app.application.services.dataloader import (
    ELLIPTIC_FEATURE_DIM,
    load_elliptic,
    resolve_dataset_dir,
)
from app.application.services.graph_embedding_model import GraphSAGEModel


@pytest.fixture
def elliptic_dir():
    return resolve_dataset_dir("elliptic")


@pytest.mark.real_data
class TestEllipticLoaderRealDataset:
    """Tests evaluating real Elliptic Bitcoin dataset files."""

    @pytest.fixture(autouse=True)
    def skip_if_no_real_dataset(self, elliptic_dir):
        parquet_cache = elliptic_dir / "elliptic_cache.parquet"
        features_csv = elliptic_dir / "elliptic_txs_features.csv"
        classes_csv = elliptic_dir / "elliptic_txs_classes.csv"
        if not parquet_cache.exists() and not (features_csv.exists() and classes_csv.exists()):
            pytest.skip("Physical Elliptic Bitcoin dataset files not present on disk.")

    def test_real_dataset_presence_and_cache(self, elliptic_dir):
        """Verify real Elliptic dataset exists with Parquet cache or CSVs."""
        parquet_cache = elliptic_dir / "elliptic_cache.parquet"
        features_csv = elliptic_dir / "elliptic_txs_features.csv"
        classes_csv = elliptic_dir / "elliptic_txs_classes.csv"

        assert parquet_cache.exists() or (features_csv.exists() and classes_csv.exists())

    def test_load_elliptic_labeled_default(self):
        """Test default loading (include_unknown=False) returns 46,564 labeled nodes."""
        data = load_elliptic(require_real=True, all_rows=True, include_unknown=False)

        assert "real" in data["source"]
        assert data["X"].shape == (46564, ELLIPTIC_FEATURE_DIM)
        assert data["y"].shape == (46564,)
        # All labels must be strictly binary
        unique_labels = set(np.unique(data["y"]))
        assert unique_labels == {0, 1}

        # Verify illicit fraud ratio in labeled set (~9.76%)
        assert 0.09 < data["fraud_ratio"] < 0.11

        # Check edge_index shape and index bounds
        assert data["edge_index"].shape[0] == 2
        assert data["edge_index"].shape[1] == len(data["edges"])
        assert data["edge_index"].shape[1] == 36624
        if len(data["edges"]) > 0:
            assert data["edge_index"].max() < 46564
            assert data["edge_index"].min() >= 0

    def test_load_elliptic_include_unknown_full_graph(self):
        """Test full graph loading (include_unknown=True) returns all 203,769 nodes and 234,355 edges."""
        data = load_elliptic(require_real=True, all_rows=True, include_unknown=True)

        assert "real" in data["source"]
        assert data["X"].shape == (203769, ELLIPTIC_FEATURE_DIM)
        assert data["y"].shape == (203769,)
        # Labels must contain -1 (unknown), 0 (licit), 1 (illicit)
        unique_labels = set(np.unique(data["y"]))
        assert unique_labels == {-1, 0, 1}

        # Check exact label distribution
        n_unknown = int(np.sum(data["y"] == -1))
        n_licit = int(np.sum(data["y"] == 0))
        n_illicit = int(np.sum(data["y"] == 1))
        assert n_unknown == 157205
        assert n_licit == 42019
        assert n_illicit == 4545
        assert n_unknown + n_licit + n_illicit == 203769

        # Verify all 234,355 directed edges are preserved
        assert data["edge_index"].shape == (2, 234355)
        assert len(data["edges"]) == 234355
        assert len(data["adjacency_lists"]) == 203769

    def test_temporal_zero_leakage_split_invariants(self):
        """Verify strict temporal split (timesteps 1-34 vs 35-49) with zero cross-split edges."""
        data = load_elliptic(
            require_real=True,
            all_rows=True,
            include_unknown=True,
            temporal_split=True,
            split_timestep=34,
        )

        assert "train_mask" in data
        assert "test_mask" in data
        assert "train_labeled_mask" in data
        assert "test_labeled_mask" in data

        train_mask = data["train_mask"]
        test_mask = data["test_mask"]
        train_labeled = data["train_labeled_mask"]
        test_labeled = data["test_labeled_mask"]
        timesteps = data["timesteps"]

        # 1. Total node counts per split
        assert int(np.sum(train_mask)) == 136265
        assert int(np.sum(test_mask)) == 67504
        assert int(np.sum(train_mask)) + int(np.sum(test_mask)) == 203769

        # 2. Labeled node counts per split (Weber et al. KDD 2019)
        assert int(np.sum(train_labeled)) == 29894
        assert int(np.sum(test_labeled)) == 16670
        assert int(np.sum(train_labeled)) + int(np.sum(test_labeled)) == 46564

        # 3. Strict temporal ordering (Zero Lookahead / Future Leakage)
        train_timesteps = timesteps[train_mask]
        test_timesteps = timesteps[test_mask]
        assert train_timesteps.max() <= 34
        assert test_timesteps.min() >= 35
        assert train_timesteps.max() < test_timesteps.min()

        # 4. Zero cross-temporal edge leakage
        edge_index = data["edge_index"]
        src_nodes = edge_index[0]
        dst_nodes = edge_index[1]

        # Invariant: Every edge must connect nodes within the exact same timestep
        src_timesteps = timesteps[src_nodes]
        dst_timesteps = timesteps[dst_nodes]
        assert np.array_equal(src_timesteps, dst_timesteps), "Edges must be strictly intra-timestep"

        # Cross edges between train and test must be exactly zero
        cross_edges = (train_mask[src_nodes] & test_mask[dst_nodes]) | (
            test_mask[src_nodes] & train_mask[dst_nodes]
        )
        assert int(np.sum(cross_edges)) == 0

        # Number of edges in train vs test subgraphs
        train_edges = train_mask[src_nodes] & train_mask[dst_nodes]
        test_edges = test_mask[src_nodes] & test_mask[dst_nodes]
        assert int(np.sum(train_edges)) == 156843
        assert int(np.sum(test_edges)) == 77512
        assert int(np.sum(train_edges)) + int(np.sum(test_edges)) == 234355

    def test_pyg_data_export(self):
        """Verify to_pyg_data() export helper generates PyTorch tensors with correct shapes."""
        data = load_elliptic(
            require_real=True,
            all_rows=True,
            include_unknown=True,
            temporal_split=True,
        )

        pyg_data = data["to_pyg_data"]()
        assert isinstance(pyg_data, dict) or hasattr(pyg_data, "x")

        # Access attributes
        x = pyg_data["x"] if isinstance(pyg_data, dict) else pyg_data.x
        y = pyg_data["y"] if isinstance(pyg_data, dict) else pyg_data.y
        edge_index = pyg_data["edge_index"] if isinstance(pyg_data, dict) else pyg_data.edge_index
        train_mask = pyg_data["train_mask"] if isinstance(pyg_data, dict) else pyg_data.train_mask

        assert isinstance(x, torch.Tensor)
        assert x.shape == (203769, 166)
        assert isinstance(y, torch.Tensor)
        assert y.shape == (203769,)
        assert isinstance(edge_index, torch.Tensor)
        assert edge_index.shape == (2, 234355)
        assert isinstance(train_mask, torch.Tensor)
        assert train_mask.shape == (203769,)

    def test_networkx_export_subgraph(self):
        """Verify to_networkx() export creates a valid NetworkX DiGraph."""
        data = load_elliptic(
            require_real=True,
            nrows=500,
            include_unknown=True,
        )

        G = data["to_networkx"](max_nodes=100)
        assert G.number_of_nodes() == 100
        # Check node attributes
        node_0 = G.nodes[0]
        assert "txId" in node_0
        assert "timestep" in node_0
        assert "label" in node_0


class TestEllipticLoaderMockAndEdgeCases:
    """Tests evaluating synthetic fixtures and fail-closed real boundary."""

    def test_synthetic_generation_preserves_schema_and_intra_timestep_edges(self):
        """Test synthetic fixture creates valid 166-feature graph with intra-timestep edges."""
        from app.application.services.synthetic_dataset_generators import (
            generate_synthetic_elliptic,
        )

        rng = np.random.default_rng(42)
        data = generate_synthetic_elliptic(
            n_mock_nodes=300,
            rng=rng,
            include_unknown=True,
            temporal_split=True,
            split_timestep=34,
        )

        assert data["source"] in ("mock", "synthetic_generator")
        assert data["is_synthetic"] is True
        assert data["provenance"] == "TEST_FIXTURE"
        assert data["X"].shape == (300, ELLIPTIC_FEATURE_DIM)
        assert data["y"].shape == (300,)
        assert set(np.unique(data["y"])).issubset({-1, 0, 1})
        assert data["edge_index"].shape[0] == 2
        assert len(data["edges"]) == data["edge_index"].shape[1]

        # Verify intra-timestep edges in synthetic graph
        if data["edge_index"].shape[1] > 0:
            src = data["edge_index"][0]
            dst = data["edge_index"][1]
            src_ts = data["timesteps"][src]
            dst_ts = data["timesteps"][dst]
            assert np.array_equal(src_ts, dst_ts)

    def test_missing_files_raises_file_not_found(self, tmp_path):
        """Verify FileNotFoundError is raised unconditionally when files are absent."""
        empty_dir = tmp_path / "empty_elliptic"
        empty_dir.mkdir()

        with pytest.raises(FileNotFoundError, match="Real Elliptic Bitcoin dataset files not found"):
            load_elliptic(path=empty_dir)

    def test_graphsage_forward_and_masked_loss_on_elliptic(self):
        """Verify GraphSAGEModel processes 166-dim Elliptic features with edge_index and masked loss."""
        # Load a manageable slice for neural execution
        data = load_elliptic(
            nrows=1000,
            include_unknown=True,
            temporal_split=True,
            split_timestep=34,
        )

        device = torch.device("cpu")
        model = GraphSAGEModel(
            input_dim=ELLIPTIC_FEATURE_DIM,
            hidden_dim=32,
            embedding_dim=16,
            num_layers=2,
        ).to(device)

        X_t = torch.from_numpy(data["X"]).to(device)
        y_t = torch.from_numpy(data["y"]).to(device)
        edge_index_t = torch.from_numpy(data["edge_index"]).to(device)
        train_labeled_mask = torch.from_numpy(data["train_labeled_mask"]).to(device)

        # Forward pass using edge_index
        embeddings, predictions = model(X_t, edge_index=edge_index_t)
        assert embeddings.shape == (1000, 16)
        assert predictions.shape == (1000,)
        assert (predictions >= 0.0).all() and (predictions <= 1.0).all()

        # Compute masked loss strictly on labeled training nodes
        loss = model.compute_loss(predictions, y_t, mask=train_labeled_mask, pos_weight=5.0)
        assert loss.item() > 0.0

        # Backward gradient flow
        loss.backward()
        for param in model.parameters():
            if param.requires_grad:
                assert param.grad is not None
