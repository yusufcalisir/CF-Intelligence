"""Clean Architecture Pydantic v2 Schemas for Health, Diagnostics, and Observability APIs."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

# ── Health & Readiness Schemas ───────────────────────────────────────────────

class DependencyHealthStatus(BaseModel):
    """Detailed health status for a single downstream infrastructure dependency."""

    status: str = Field(..., description="Component status: HEALTHY, DEGRADED, or DOWN")
    latency_ms: float = Field(default=0.0, description="Response latency in milliseconds")
    message: str | None = Field(default=None, description="Diagnostic detail or failure message")


class HealthCheckResponse(BaseModel):
    """Liveness probe response."""

    status: str = Field(..., description="Overall process health status, e.g. 'healthy'")
    service: str = Field(default="fraud-intelligence-api", description="Service identifier")
    timestamp: str = Field(..., description="UTC ISO8601 evaluation timestamp")
    version: str = Field(default="2.4.0", description="Semantic service release version")
    uptime_seconds: float = Field(default=0.0, description="Process uptime in seconds")


class ReadinessResponse(BaseModel):
    """Readiness probe response verifying database, cache, vault, and enclave connectivity."""

    status: str = Field(..., description="Overall readiness: 'ready' or 'degraded'")
    checks: dict[str, Any] = Field(..., description="Component health checks mapping")
    timestamp: str = Field(..., description="UTC ISO8601 evaluation timestamp")


class LivenessResponse(BaseModel):
    """Kubernetes liveness probe response."""

    status: str = Field(default="alive", description="Liveness state")
    timestamp: str = Field(..., description="UTC ISO8601 evaluation timestamp")


# ── Diagnostics Schemas ─────────────────────────────────────────────────────

class MemoryDiagnostic(BaseModel):
    """Host virtual memory breakdown."""

    total_mb: float = Field(..., description="Total system physical memory in MB")
    available_mb: float = Field(..., description="Available system memory in MB")
    used_mb: float = Field(..., description="Used system memory in MB")
    percent: float = Field(..., description="Memory utilization percentage")


class ProcessMemoryDiagnostic(BaseModel):
    """Process resident set size and virtual memory allocation."""

    rss_mb: float = Field(..., description="Resident Set Size in MB")
    vms_mb: float = Field(..., description="Virtual Memory Size in MB")


class SystemDiagnosticResponse(BaseModel):
    """Detailed system diagnostic overview."""

    platform: str = Field(..., description="OS platform and kernel release")
    python_version: str = Field(..., description="Python runtime version")
    cpu_count: int = Field(..., description="Number of logical CPU cores")
    memory: MemoryDiagnostic = Field(..., description="System RAM metrics")
    process_memory: ProcessMemoryDiagnostic = Field(..., description="API process RAM metrics")
    uptime_seconds: float = Field(..., description="Process uptime in seconds")
    timestamp: str = Field(..., description="UTC ISO8601 diagnostic timestamp")


class EnvironmentDiagnosticResponse(BaseModel):
    """Sanitized runtime configuration flags and environment classification."""

    environment: str = Field(..., description="Deployment tier: production, staging, or test")
    python_env: str = Field(..., description="Virtual environment identifier")
    debug: bool = Field(default=False, description="Debug mode state")
    log_level: str = Field(default="INFO", description="Configured logging level")
    secure_mode: bool = Field(default=True, description="Strict TLS/mTLS security flag")
    timestamp: str = Field(..., description="UTC ISO8601 timestamp")


class ProcessMemoryResponse(BaseModel):
    """Detailed process memory and cache sizing metrics."""

    rss_mb: float = Field(..., description="Resident Set Size in MB")
    vms_mb: float = Field(..., description="Virtual Memory Size in MB")
    cache_entries: int = Field(default=0, description="Active items in shared memory cache")
    timestamp: str = Field(..., description="UTC ISO8601 timestamp")


class ConnectorProbeRequest(BaseModel):
    """Payload to trigger an active probe test on an enterprise connector."""

    connector_id: str = Field(..., min_length=1, max_length=64, description="Connector identifier (e.g., kafka, vault, kms, redis, database)")


class ConnectorHealthSummary(BaseModel):
    """Summary of a specific connector's health."""

    connector_id: str
    status: str
    latency_ms: float
    protocol: str
    endpoint: str
    details: dict[str, Any] = Field(default_factory=dict)


class DiagnosticsOverviewResponse(BaseModel):
    """Response schema containing health metrics for all enterprise connectors."""

    total_connectors: int
    healthy_connectors: int
    avg_latency_ms: float
    connectors: list[ConnectorHealthSummary]


class ConnectorTestProbeResult(BaseModel):
    """Result of an on-demand active connectivity test probe."""

    connector_id: str
    name: str = ""
    success: bool
    status_code: int
    round_trip_ms: float
    handshake_summary: str = ""
    diagnostics_log: list[str]
    payload_sample: dict[str, Any] = Field(default_factory=dict)


# ── Observability & Drift Monitoring Schemas ────────────────────────────────

class FeatureDriftResponse(BaseModel):
    """Single feature drift evaluation result."""

    feature_name: str = Field(..., description="Feature column identifier")
    ks_statistic: float = Field(..., description="Kolmogorov-Smirnov two-sample statistic")
    ks_p_value: float = Field(..., description="KS test p-value")
    wasserstein_distance: float = Field(..., description="First Wasserstein (Earth Mover's) distance")
    psi: float = Field(..., description="Population Stability Index")
    status: str = Field(..., description="Drift classification: STABLE, MODERATE_DRIFT, or SEVERE_DRIFT")


class CalibrationBinResponse(BaseModel):
    """Reliability diagram calibration bin."""

    bin_index: int
    prob_min: float
    prob_max: float
    mean_predicted_prob: float
    empirical_fraud_ratio: float
    sample_count: int


class CalibrationResponse(BaseModel):
    """Probability calibration analysis response."""

    brier_score: float = Field(..., description="Brier score (mean squared error of probability forecasts)")
    expected_calibration_error: float = Field(..., description="Expected Calibration Error (ECE)")
    max_calibration_error: float = Field(..., description="Maximum Calibration Error (MCE)")
    is_well_calibrated: bool = Field(..., description="Whether model meets calibration tolerance")
    evaluated_at: str = Field(..., description="UTC ISO8601 evaluation timestamp")
    bins: list[CalibrationBinResponse] = Field(default_factory=list)


class DriftAnalysisResponse(BaseModel):
    """Comprehensive statistical feature and concept drift report."""

    overall_status: str = Field(..., description="System drift status: STABLE, WARNING, or CRITICAL")
    max_psi: float = Field(..., description="Maximum feature PSI across analyzed features")
    mean_ks_p_value: float = Field(..., description="Mean KS test p-value")
    concept_drift_psi: float = Field(..., description="Overall model concept drift PSI")
    auto_retrain_triggered: bool = Field(default=False, description="Whether automated retraining was triggered")
    evaluated_at: str = Field(..., description="UTC ISO8601 evaluation timestamp")
    feature_drifts: list[FeatureDriftResponse] = Field(default_factory=list)
    calibration: CalibrationResponse | None = Field(default=None)


class DriftEvaluationRequest(BaseModel):
    """Payload to evaluate drift on live custom feature distributions."""

    current_features: dict[str, list[float]]
    reference_features: dict[str, list[float]]
    current_risk_scores: list[float]
    reference_risk_scores: list[float]
    ground_truth_labels: list[int] | None = None
    predicted_probabilities: list[float] | None = None


class ConceptDriftPsiResponse(BaseModel):
    """Population Stability Index (PSI) and Wasserstein distance drift metrics."""

    concept_drift_psi: float = Field(..., description="Global concept drift PSI")
    overall_status: str = Field(..., description="Drift status: STABLE, WARNING, or CRITICAL")
    alert_level: str = Field(..., description="Alert severity: NORMAL, WARNING, or CRITICAL")
    max_feature_psi: float = Field(..., description="Maximum observed feature PSI")
    evaluated_at: str = Field(..., description="UTC ISO8601 evaluation timestamp")
    features: dict[str, float] = Field(default_factory=dict, description="Per-feature PSI metrics")


class FairnessMetricsResponse(BaseModel):
    """Model algorithmic fairness and demographic parity metrics across consortium institutions."""

    demographic_parity_ratio: float = Field(..., description="Ratio of positive outcomes across protected groups")
    disparate_impact_ratio: float = Field(..., description="EEOC four-fifths rule ratio (target >= 0.80)")
    equalized_odds_difference: float = Field(..., description="Maximum difference in TPR and FPR across groups")
    satisfies_four_fifths_rule: bool = Field(..., description="True if disparate impact ratio >= 0.80")
    evaluated_at: str = Field(..., description="UTC ISO8601 evaluation timestamp")
    protected_attributes: list[str] = Field(default_factory=list, description="Audited protected demographic attributes")


class TelemetryOverviewResponse(BaseModel):
    """System-wide telemetry counters, latency distributions, and Prometheus scrape summary."""

    uptime_seconds: float = Field(..., description="Process uptime in seconds")
    active_requests: int = Field(default=0, description="Currently in-flight HTTP requests")
    metrics_scraped_total: int = Field(default=0, description="Total Prometheus metrics scrapings")
    alerts_firing: int = Field(default=0, description="Count of currently firing alerts")
    timestamp: str = Field(..., description="UTC ISO8601 timestamp")


# ── Alerting & Retraining Schemas ───────────────────────────────────────────

class ActiveAlertResponse(BaseModel):
    """Prometheus Alertmanager alert descriptor."""

    alert_name: str
    severity: str
    summary: str
    started_at: str
    status: str


class AlertmanagerAlert(BaseModel):
    status: str = "firing"
    labels: dict[str, str] = Field(default_factory=dict)
    annotations: dict[str, str] = Field(default_factory=dict)
    startsAt: str | None = None
    endsAt: str | None = None


class AlertmanagerWebhookPayload(BaseModel):
    version: str | None = "4"
    groupKey: str | None = None
    status: str = "firing"
    receiver: str | None = None
    alerts: list[AlertmanagerAlert] = Field(default_factory=list)


class RetrainTriggerRequest(BaseModel):
    """Payload to trigger an automated federated retraining round in response to drift."""

    reason: str = Field(default="Concept Drift PSI > 0.20 threshold exceeded", description="Reason for triggering retraining")
    retrain_feature_subset: list[str] = Field(default_factory=list, description="Target features experiencing statistical or concept drift")
    max_psi: float | None = Field(default=None, description="Observed peak population stability index")
    dispatch_alertmanager_webhook: bool = Field(default=True, description="Whether to dispatch Prometheus Alertmanager alert")
    target_simulation_rounds: int = Field(default=3, ge=1, le=50, description="Target federated training rounds")


class RetrainTriggerResponse(BaseModel):
    triggered: bool
    reason: str
    new_simulation_id: str | None = None
    triggered_at: str
    retrain_feature_subset: list[str] = Field(default_factory=list, description="Features included in retraining scope")
    alertmanager_alert_dispatched: bool = Field(default=False, description="Flag indicating Alertmanager alert was registered")
    prometheus_metric_emitted: bool = Field(default=True, description="Flag indicating Prometheus drift metrics were updated")
    drift_features_targeted: int = Field(default=0, description="Count of drifted features in retraining payload")


class SiemExportRequest(BaseModel):
    """Payload to configure and trigger SIEM metric export."""

    format: str = Field(default="json", description="SIEM export format: json or cef (Common Event Format)")
    include_drift_metrics: bool = Field(default=True, description="Whether to include feature drift metrics")
    include_alerts: bool = Field(default=True, description="Whether to include active Alertmanager alerts")


class SiemExportResponse(BaseModel):
    """SIEM format export output."""

    format: str
    exported_at: str
    event_count: int
    payload: str


class PrometheusMetricsExportResponse(BaseModel):
    """Prometheus exposition format container."""

    metrics_text: str
    metric_count: int
    scraped_at: str


class RetrainingJobResponse(BaseModel):
    job_id: str
    cause: str
    psi_score: float
    status: str
    candidate_model_version: str | None = None
    triggered_at: str
    details: dict[str, Any] = Field(default_factory=dict)
