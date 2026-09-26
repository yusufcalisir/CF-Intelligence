/**
 * TypeScript contracts mirroring Python Experiment Harness schemas.
 * (experiments/harness/schema.py <-> frontend/src/types/benchmark.ts)
 */

export interface HardwareMetadata {
  os_platform: string;
  os_release: string;
  os_version: string;
  cpu_model: string;
  cpu_architecture: string;
  cpu_physical_cores: number;
  cpu_logical_cores: number;
  total_ram_gb: number;
  available_ram_gb: number;
  python_version: string;
  torch_version: string;
  cuda_available: boolean;
  device_name: string;
}

export interface DatasetMetadata {
  dataset_name: string;
  source_uri?: string | null;
  sha256_hash: string;
  total_samples: number;
  num_features: number;
  fraud_samples: number;
  fraud_rate: number;
  split_ratios: Record<string, number>;
}

export interface ExperimentConfig {
  experiment_id: string;
  experiment_name: string;
  description: string;
  tags: string[];
  model_type: string;
  strategy: string;
  seeds: number[];
  num_rounds: number;
  local_epochs: number;
  batch_size: number;
  learning_rate: number;
  dp_enabled: boolean;
  dp_epsilon?: number | null;
  dp_delta?: number | null;
  dp_max_grad_norm?: number | null;
  hyperparameters: Record<string, unknown>;
  output_dir: string;
}

export interface StepMetric {
  step: number;
  train_loss?: number | null;
  val_loss?: number | null;
  pr_auc?: number | null;
  roc_auc?: number | null;
  accuracy?: number | null;
  f1_score?: number | null;
  precision?: number | null;
  recall?: number | null;
  duration_seconds: number;
  timestamp_utc: string;
  extra?: Record<string, unknown>;
}

export interface CurvePoint {
  fpr: number[];
  tpr: number[];
  precision: number[];
  recall: number[];
  thresholds?: number[];
}

export interface ConfusionMatrixData {
  tn: number;
  fp: number;
  fn: number;
  tp: number;
  labels: string[];
}

export interface CalibrationData {
  prob_true: number[];
  prob_pred: number[];
  brier_score: number;
}

export interface ExperimentResult {
  schema_version: string;
  experiment_id: string;
  config: ExperimentConfig;
  hardware: HardwareMetadata;
  dataset: DatasetMetadata;
  git_commit: string;
  git_branch: string;
  status: 'PENDING' | 'RUNNING' | 'COMPLETED' | 'FAILED';
  start_time_utc: string;
  end_time_utc: string;
  total_duration_seconds: number;
  final_metrics: Record<string, number>;
  history: StepMetric[];
  curves?: CurvePoint | null;
  confusion_matrix?: ConfusionMatrixData | null;
  calibration?: CalibrationData | null;
  artifact_paths: Record<string, string>;
}

export interface AggregateMetric {
  mean: number;
  std: number;
  min: number;
  max: number;
  ci_95_lower: number;
  ci_95_upper: number;
}

export interface AggregateSummary {
  experiment_name: string;
  num_runs: number;
  seeds: number[];
  metrics: Record<string, AggregateMetric>;
  individual_results: string[];
}

export interface PublicationPlotMetadata {
  plot_id: string;
  title: string;
  figure_type: 'roc' | 'pr' | 'calibration' | 'confusion_matrix' | 'convergence' | 'tradeoff';
  relative_path: string;
  metric_summary?: string;
}
