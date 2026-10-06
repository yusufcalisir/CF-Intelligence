"""Domain value objects.

Immutable data containers that carry meaning but have no identity.
These are passed between services and serialized to API responses.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ModelWeights:
    """Serializable representation of neural network parameters.

    Stores flattened parameter tensors as lists of floats.
    In production FL, these would be serialized via protobuf.
    Here we use plain Python for transparency.
    """

    layer_shapes: list[tuple[int, ...]]
    flat_weights: list[float]

    @property
    def num_parameters(self) -> int:
        return len(self.flat_weights)


@dataclass(frozen=True)
class EvaluationMetrics:
    """Metrics from evaluating a fraud detection model.

    All values are computed on a held-out test set per bank.
    Fairness and defense metrics default to None when uncalculated.
    """

    accuracy: float
    precision: float
    recall: float
    f1_score: float
    auc_roc: float | None = None
    loss: float = 0.0

    # Confusion matrix: [[TN, FP], [FN, TP]]
    confusion_matrix: list[list[int]] = field(default_factory=lambda: [[0, 0], [0, 0]])

    # ROC curve data points for plotting
    roc_fpr: list[float] = field(default_factory=list)
    roc_tpr: list[float] = field(default_factory=list)
    roc_thresholds: list[float] = field(default_factory=list)

    # Feature importance (absolute weight magnitude from first layer)
    feature_importance: dict[str, float] = field(default_factory=dict)

    # Federated Fairness Audit metrics (EU AI Act compliance)
    disparate_impact: float | None = None
    equal_opportunity_diff: float | None = None
    protected_selection_rate: float | None = None
    reference_selection_rate: float | None = None

    # Active Defense & Adversarial Training metrics
    adversarial_robustness_score: float | None = None
    clean_accuracy: float | None = None
    robust_accuracy: float | None = None
    fgsm_evasion_rate: float | None = None
    pgd_evasion_rate: float | None = None

    # Operating point and threshold provenance
    threshold: float = 0.5
    pr_auc: float | None = None
    predicted_positives: int = 0
    threshold_provenance: str = "default_fixed_0.5"
    dp_provenance: str | None = None
    auc_roc_defined: bool = True
    auc_roc_status: str = "defined"


@dataclass(frozen=True)
class RoundMetrics:
    """Metrics collected during a single federated training round."""

    round_number: int
    global_loss: float
    participating_banks: list[str]
    dropped_banks: list[str]
    per_bank_loss: dict[str, float]
    per_bank_samples: dict[str, int]
    aggregation_time_ms: float
    round_duration_ms: float
    privacy_budget_spent: float = 0.0


@dataclass(frozen=True)
class BankDataProfile:
    """Statistical profile of a bank's dataset.

    Used to demonstrate Non-IID distribution without exposing raw data.
    """

    bank_name: str
    num_transactions: int
    fraud_ratio: float
    mean_transaction_amount: float
    std_transaction_amount: float
    top_merchant_categories: list[str]
    top_countries: list[str]
    mean_account_age_days: float
    mean_velocity: float


@dataclass(frozen=True)
class SimulationConfig:
    """User-configurable parameters for a simulation run."""

    num_rounds: int = 10
    local_epochs: int = 3
    learning_rate: float = 0.001
    batch_size: int = 64
    min_clients_per_round: int = 2

    # Failure simulation
    enable_latency_simulation: bool = False
    latency_range_ms: tuple[int, int] = (50, 500)
    enable_dropout_simulation: bool = False
    dropout_probability: float = 0.2
    enable_reconnect_simulation: bool = True

    # Privacy
    enable_differential_privacy: bool = False
    dp_epsilon: float = 0.75
    dp_epsilon_limit: float = 8.0
    dp_delta: float = 1e-5
    dp_max_grad_norm: float = 0.5
    dp_mode: str = "post_hoc"
    enable_secure_aggregation: bool = False
    dp_learning_rate: float | None = None
    dp_local_epochs: int | None = None

    # Active Defense & Adversarial Training
    enable_adversarial_training: bool = False
    adversarial_attack_type: str = "fgsm"
    adversarial_epsilon: float = 0.05
    adversarial_alpha: float = 0.01
    adversarial_steps: int = 5
    adversarial_loss_weight: float = 0.5

    # Data
    dataset: str = "synthetic"  # "synthetic", "paysim", "ieee_cis", "elliptic", "creditcard"
    dataset_mode: str | None = None  # "real", "synthetic", or None (auto: 'real' for registered benchmark, 'synthetic' for generator)
    bank_a_transactions: int = 50000
    bank_b_transactions: int = 30000
    bank_c_transactions: int = 20000

    # Aggregation strategy
    aggregation_method: str = "fed_avg_weighted"

    # FL engine selection
    fl_engine_type: str = "custom"
    require_flower_backend: bool = False
    allow_native_fallback: bool = True
    p2p_mode: bool = False
    topology: str = "RING"  # "RING" or "MESH"

    # Adversarial / poisoning simulation
    enable_poisoning_simulation: bool = False
    poisoning_bank_id: str = "bank_c"
    poisoning_scale: float = 5.0
    byzantine_defense: str = "none"  # "none", "krum", "coordinate_wise_median"

    # Operating point threshold calibration
    threshold_policy: str = "max_f1"  # "max_f1", "youden", "fixed_0.5"
    validation_split_ratio: float = 0.15

    # Federated Graph Embedding (FedGNN)
    enable_graph_embedding: bool = False
    gnn_embedding_dim: int = 64
    gnn_hidden_dim: int = 128
    gnn_num_layers: int = 2
    gnn_epochs_per_round: int = 5
    gnn_learning_rate: float = 0.01
    gnn_neighbor_sample_size: int = 10

    # Advanced Federated Optimization
    fedprox_mu: float = 0.0
    moon_mu: float = 0.0
    moon_temperature: float = 0.5
    fedopt_server_lr: float = 0.01
    fedopt_beta1: float = 0.9
    fedopt_beta2: float = 0.999
    fedopt_tau: float = 1e-3

    # Bias mitigation & regulatory fairness
    enable_bias_mitigation: bool = False
    fairness_lambda: float = 0.5

    # Hardware & Cryptographic Isolation
    hardware_isolation_mode: str = "none"  # "none", "tee", "fhe"

    # Real-Time Streaming GNN Settings
    enable_streaming_gnn: bool = False

    # Web3 & CBDC Smart Contract Incentive Settlement
    enable_web3_settlement: bool = False
    settlement_currency: str = "wCBDC"
    smart_contract_address: str = "0x71C7656EC7ab88b098defB751B7401B5f6d8976F"
