"""Temporal Generalization & Out-of-Time Degradation Experiment Suite.

Provides tools for evaluating model generalization across chronological periods:
- Past-Present-Future Split Protocol (Period 1 vs Period 2 vs Period 3)
- Optimistic Random K-Fold Cross-Validation vs Strict Out-of-Time Degradation
- Population Stability Index (PSI) and Kolmogorov-Smirnov Feature Drift Tracking
"""

from __future__ import annotations

from experiments.temporal.temporal_generalization import (
    FeatureDriftProfile,
    KFoldMetrics,
    PeriodMetrics,
    TemporalDegradationMetrics,
    TemporalGeneralizationConfig,
    TemporalGeneralizationSuiteResult,
    TemporalSplitProtocol,
    calculate_feature_drift,
    format_temporal_benchmark_markdown,
    generate_temporal_stream,
    run_temporal_generalization_benchmark,
)

__all__ = [
    "FeatureDriftProfile",
    "KFoldMetrics",
    "PeriodMetrics",
    "TemporalDegradationMetrics",
    "TemporalGeneralizationConfig",
    "TemporalGeneralizationSuiteResult",
    "TemporalSplitProtocol",
    "calculate_feature_drift",
    "format_temporal_benchmark_markdown",
    "generate_temporal_stream",
    "run_temporal_generalization_benchmark",
]
