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
    """

    accuracy: float
    precision: float
    recall: float
    f1_score: float
    auc_roc: float
    loss: float

    # Confusion matrix: [[TN, FP], [FN, TP]]
    confusion_matrix: list[list[int]]

    # ROC curve data points for plotting
    roc_fpr: list[float]
    roc_tpr: list[float]
    roc_thresholds: list[float]

    # Feature importance (absolute weight magnitude from first layer)
    feature_importance: dict[str, float] = field(default_factory=dict)

    # Federated Fairness Audit metrics (EU AI Act compliance)
    disparate_impact: float = 1.0
    equal_opportunity_diff: float = 0.0
    protected_selection_rate: float = 1.0
    reference_selection_rate: float = 1.0

    # Active Defense & Adversarial Training metrics
    adversarial_robustness_score: float = 1.0
    clean_accuracy: float = 0.0
    robust_accuracy: float = 0.0
    fgsm_evasion_rate: float = 0.0
    pgd_evasion_rate: float = 0.0


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
    dp_epsilon: float = 1.0
    dp_epsilon_limit: float = 8.0
    dp_delta: float = 1e-5
    dp_max_grad_norm: float = 1.0
    dp_mode: str = "post_hoc"
    enable_secure_aggregation: bool = False

    # Active Defense & Adversarial Training
    enable_adversarial_training: bool = False
    adversarial_attack_type: str = "fgsm"
    adversarial_epsilon: float = 0.05
    adversarial_alpha: float = 0.01
    adversarial_steps: int = 5
    adversarial_loss_weight: float = 0.5

    # Data
    dataset: str = "synthetic"  # "synthetic", "paysim", "ieee_cis", "elliptic", "creditcard"
    bank_a_transactions: int = 50000
    bank_b_transactions: int = 30000
    bank_c_transactions: int = 20000

    # Aggregation strategy
    aggregation_method: str = "fed_avg_weighted"

    # FL engine selection
    fl_engine_type: str = "custom"

    # Adversarial / poisoning simulation
    enable_poisoning_simulation: bool = False
    poisoning_bank_id: str = "bank_c"
    poisoning_scale: float = 5.0
    byzantine_defense: str = "none"  # "none", "krum", "coordinate_wise_median"

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
