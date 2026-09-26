"""Schema definitions for Unified Experiment Tracking and Serialization.

Provides robust Pydantic v2 schemas for hardware environments, dataset metadata,
hyperparameter configurations, step-by-step metrics, evaluation curves,
and final machine-readable experiment results.
"""

from __future__ import annotations

import hashlib
import os
import platform
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class HardwareMetadata(BaseModel):
    """Execution environment hardware and platform metadata."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    os_platform: str = Field(description="Operating system name, e.g. Windows, Linux")
    os_release: str = Field(description="Operating system release")
    os_version: str = Field(description="Operating system version string")
    cpu_model: str = Field(description="Processor / CPU model name")
    cpu_architecture: str = Field(description="Processor architecture, e.g. x86_64, AMD64")
    cpu_physical_cores: int = Field(description="Number of physical CPU cores")
    cpu_logical_cores: int = Field(description="Number of logical CPU cores / threads")
    total_ram_gb: float = Field(description="Total system RAM in Gigabytes")
    available_ram_gb: float = Field(description="Available system RAM at execution start")
    python_version: str = Field(description="Python runtime version")
    torch_version: str = Field(description="PyTorch library version")
    cuda_available: bool = Field(description="Whether CUDA acceleration is available")
    device_name: str = Field(description="Compute device name (e.g. CPU or GPU model)")

    @classmethod
    def capture(cls) -> HardwareMetadata:
        """Automatically probe and construct hardware metadata from the current system."""
        cpu_model = platform.processor() or "Unknown"
        cpu_arch = platform.machine() or "Unknown"
        physical_cores = os.cpu_count() or 1
        logical_cores = os.cpu_count() or 1
        total_ram = 16.0
        avail_ram = 8.0

        try:
            import psutil
            logical_cores = psutil.cpu_count(logical=True) or logical_cores
            physical_cores = psutil.cpu_count(logical=False) or physical_cores
            mem = psutil.virtual_memory()
            total_ram = round(mem.total / (1024**3), 2)
            avail_ram = round(mem.available / (1024**3), 2)
        except Exception:
            pass

        torch_ver = "N/A"
        cuda_avail = False
        device = "CPU"
        try:
            import torch
            torch_ver = torch.__version__
            cuda_avail = torch.cuda.is_available()
            device = torch.cuda.get_device_name(0) if cuda_avail else "CPU"
        except Exception:
            pass

        return cls(
            os_platform=platform.system(),
            os_release=platform.release(),
            os_version=platform.version(),
            cpu_model=cpu_model,
            cpu_architecture=cpu_arch,
            cpu_physical_cores=physical_cores,
            cpu_logical_cores=logical_cores,
            total_ram_gb=total_ram,
            available_ram_gb=avail_ram,
            python_version=platform.python_version(),
            torch_version=torch_ver,
            cuda_available=cuda_avail,
            device_name=device,
        )


class DatasetMetadata(BaseModel):
    """Metadata describing the dataset used in an experiment run."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    dataset_name: str = Field(description="Canonical name of the dataset")
    source_uri: str | None = Field(default=None, description="Path or URL to dataset source")
    sha256_hash: str = Field(description="SHA-256 hash of dataset file or partition representation")
    total_samples: int = Field(description="Total record count")
    num_features: int = Field(description="Feature column count")
    fraud_samples: int = Field(default=0, description="Positive / fraud class count")
    fraud_rate: float = Field(default=0.0, description="Fraud prevalence ratio [0.0, 1.0]")
    split_ratios: dict[str, float] = Field(
        default_factory=lambda: {"train": 0.70, "val": 0.15, "test": 0.15},
        description="Partition split ratios",
    )

    @classmethod
    def from_bytes(
        cls,
        data: bytes,
        dataset_name: str,
        total_samples: int,
        num_features: int,
        fraud_samples: int = 0,
        source_uri: str | None = None,
        split_ratios: dict[str, float] | None = None,
    ) -> DatasetMetadata:
        """Compute SHA256 from raw data buffer and construct dataset metadata."""
        hasher = hashlib.sha256(data)
        digest = hasher.hexdigest()
        fraud_rate = fraud_samples / max(1, total_samples)
        return cls(
            dataset_name=dataset_name,
            source_uri=source_uri,
            sha256_hash=digest,
            total_samples=total_samples,
            num_features=num_features,
            fraud_samples=fraud_samples,
            fraud_rate=round(fraud_rate, 6),
            split_ratios=split_ratios or {"train": 0.70, "val": 0.15, "test": 0.15},
        )


class ExperimentConfig(BaseModel):
    """Complete hyperparameter and run configuration."""

    model_config = ConfigDict(extra="ignore")

    experiment_id: str = Field(description="Unique experiment run identifier")
    experiment_name: str = Field(description="Human-readable experiment name")
    description: str = Field(default="", description="Detailed description of experiment objective")
    tags: list[str] = Field(default_factory=list, description="Categorical tags for filtering")
    model_type: str = Field(description="Algorithm or architecture class, e.g. FederatedMLP, GraphSAGE")
    strategy: str = Field(default="FedAvg", description="Federated or centralized optimization strategy")
    seeds: list[int] = Field(default_factory=lambda: [42], description="Random seed(s)")
    num_rounds: int = Field(default=10, description="Total federated rounds or training epochs")
    local_epochs: int = Field(default=3, description="Client local epochs per round")
    batch_size: int = Field(default=64, description="Training batch size")
    learning_rate: float = Field(default=0.001, description="Learning rate")
    dp_enabled: bool = Field(default=False, description="Whether differential privacy is applied")
    dp_epsilon: float | None = Field(default=None, description="Target privacy budget epsilon")
    dp_delta: float | None = Field(default=None, description="Target privacy slack delta")
    dp_max_grad_norm: float | None = Field(default=None, description="L2 gradient clipping bound")
    hyperparameters: dict[str, Any] = Field(
        default_factory=dict, description="Arbitrary additional algorithm hyperparameters"
    )
    output_dir: str = Field(
        default="experiments/results", description="Root directory where artifacts are saved"
    )


class StepMetric(BaseModel):
    """Step-by-step metric point recorded during training or evaluation."""

    model_config = ConfigDict(extra="ignore")

    step: int = Field(description="Step index (epoch or federated round)")
    train_loss: float | None = Field(default=None, description="Training loss at step")
    val_loss: float | None = Field(default=None, description="Validation loss at step")
    pr_auc: float | None = Field(default=None, description="Precision-Recall AUC at step")
    roc_auc: float | None = Field(default=None, description="ROC AUC at step")
    accuracy: float | None = Field(default=None, description="Accuracy at step")
    f1_score: float | None = Field(default=None, description="F1-score at step")
    precision: float | None = Field(default=None, description="Precision at step")
    recall: float | None = Field(default=None, description="Recall at step")
    duration_seconds: float = Field(default=0.0, description="Step duration in seconds")
    timestamp_utc: str = Field(
        default_factory=lambda: datetime.now(UTC).isoformat(),
        description="ISO 8601 UTC timestamp of step completion",
    )
    extra: dict[str, Any] = Field(default_factory=dict, description="Custom step signals")


class CurvePoint(BaseModel):
    """Discretized points along evaluation curves for charting."""

    model_config = ConfigDict(extra="ignore")

    fpr: list[float] = Field(default_factory=list, description="False Positive Rates")
    tpr: list[float] = Field(default_factory=list, description="True Positive Rates")
    precision: list[float] = Field(default_factory=list, description="Precision values")
    recall: list[float] = Field(default_factory=list, description="Recall values")
    thresholds: list[float] = Field(default_factory=list, description="Decision thresholds")


class ConfusionMatrixData(BaseModel):
    """Binary confusion matrix components and class labels."""

    model_config = ConfigDict(extra="ignore")

    tn: int = Field(default=0, description="True Negatives")
    fp: int = Field(default=0, description="False Positives")
    fn: int = Field(default=0, description="False Negatives")
    tp: int = Field(default=0, description="True Positives")
    labels: list[str] = Field(
        default_factory=lambda: ["Legitimate", "Fraud"],
        description="Class labels [negative, positive]",
    )

    @property
    def total(self) -> int:
        return self.tn + self.fp + self.fn + self.tp


class CalibrationData(BaseModel):
    """Empirical calibration and reliability diagram metrics."""

    model_config = ConfigDict(extra="ignore")

    prob_true: list[float] = Field(default_factory=list, description="Empirical fraud rate per bin")
    prob_pred: list[float] = Field(default_factory=list, description="Mean predicted probability per bin")
    brier_score: float = Field(default=0.0, description="Brier score loss")


class ExperimentResult(BaseModel):
    """Complete machine-readable experiment execution result."""

    model_config = ConfigDict(extra="ignore")

    schema_version: str = Field(default="1.0.0", description="Result schema specification version")
    experiment_id: str = Field(description="Unique experiment identifier")
    config: ExperimentConfig = Field(description="Configuration used to produce this run")
    hardware: HardwareMetadata = Field(description="Hardware environment metadata")
    dataset: DatasetMetadata = Field(description="Dataset metadata and integrity hash")
    git_commit: str = Field(description="Git commit SHA-1 hash of codebase at execution")
    git_branch: str = Field(default="main", description="Git branch name at execution")
    status: str = Field(default="COMPLETED", description="Run status: RUNNING, COMPLETED, FAILED")
    start_time_utc: str = Field(description="ISO 8601 execution start timestamp")
    end_time_utc: str = Field(description="ISO 8601 execution end timestamp")
    total_duration_seconds: float = Field(description="Total elapsed execution time in seconds")
    final_metrics: dict[str, float] = Field(
        default_factory=dict, description="Summary evaluation metrics (ROC-AUC, PR-AUC, etc.)"
    )
    history: list[StepMetric] = Field(
        default_factory=list, description="Chronological round-by-round metric progression"
    )
    curves: CurvePoint | None = Field(default=None, description="ROC and PR curve coordinates")
    confusion_matrix: ConfusionMatrixData | None = Field(
        default=None, description="Confusion matrix metrics"
    )
    calibration: CalibrationData | None = Field(
        default=None, description="Calibration reliability metrics"
    )
    artifact_paths: dict[str, str] = Field(
        default_factory=dict, description="Relative paths to saved artifacts (JSON, CSV, Parquet, plots)"
    )


class AggregateMetric(BaseModel):
    """Statistical aggregation of a single metric across multiple random seeds."""

    model_config = ConfigDict(extra="ignore")

    mean: float = Field(description="Mean value across seeds")
    std: float = Field(description="Sample standard deviation")
    min: float = Field(description="Minimum observed value")
    max: float = Field(description="Maximum observed value")
    ci_95_lower: float = Field(description="Lower bound of 95% confidence interval")
    ci_95_upper: float = Field(description="Upper bound of 95% confidence interval")


class AggregateSummary(BaseModel):
    """Multi-seed aggregate summary report."""

    model_config = ConfigDict(extra="ignore")

    experiment_name: str = Field(description="Name of experiment family")
    num_runs: int = Field(description="Number of seed executions included")
    seeds: list[int] = Field(description="Random seeds evaluated")
    metrics: dict[str, AggregateMetric] = Field(description="Aggregated metrics dictionary")
    individual_results: list[str] = Field(
        default_factory=list, description="List of experiment_ids included in this aggregate"
    )
