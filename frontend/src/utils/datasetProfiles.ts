/**
 * datasetProfiles.ts
 *
 * Central registry of benchmark dataset profiles used by the dataset-aware
 * federated training engine. Each profile contains:
 *   - Display metadata (label, badge, source link, icon, color)
 *   - Dataset statistics (sample count, fraud ratio, feature count)
 *   - Mock simulation parameters (AUC/loss convergence characteristics)
 *
 * The "Real Backend" mode ignores simulation params and routes to the live
 * WebSocket endpoint — these profiles are used exclusively by the mock engine.
 */

export interface DatasetProfile {
  id: 'paysim' | 'ieee_cis' | 'elliptic' | 'creditcard';
  label: string;
  subtitle: string;
  badge: string;
  sourceLink: string;
  /** Tailwind-compatible hex accent color for chart lines / card borders */
  color: string;
  /** Emoji icon for compact display */
  icon: string;

  // ── Dataset statistics ─────────────────────────────────────────────────────
  totalSamples: number;
  /** Fraud ratio 0–1, e.g. 0.013 = 1.3% */
  fraudRatio: number;
  numFeatures: number;
  fraudPattern: string;

  // ── Mock simulation convergence profile ───────────────────────────────────
  /** AUC at the start of round 1 */
  initialAuc: number;
  /** Asymptotic AUC ceiling across TOTAL_ROUNDS rounds */
  targetAuc: number;
  /** Mean AUC gain per federated round */
  aucStepMean: number;
  /** Gaussian noise std on AUC per round (simulates cross-bank variance) */
  aucStepStd: number;
  /** Loss at round 0 */
  initialLoss: number;
  /** Loss floor (won't go below this) */
  targetLoss: number;
  /** Multiplicative decay fraction per round (e.g. 0.034 → 3.4% drop/round) */
  lossDecayRate: number;
  /** Per-bank AUC offset std (spread between bank models) */
  bankSpreadStd: number;

  // ── Initial header display (before training begins) ───────────────────────
  championAucDefault: number;
}

export const DATASET_PROFILES: Record<DatasetProfile['id'], DatasetProfile> = {
  paysim: {
    id: 'paysim',
    label: 'PaySim Mobile Money',
    subtitle: 'Kenya M-Pesa · 6.36M transactions · 11 features',
    badge: 'Academic Standard · Kaggle: ealaxi/paysim1',
    sourceLink: 'https://www.kaggle.com/datasets/ealaxi/paysim1',
    color: '#6366f1',   // indigo — matches platform primary
    icon: '📱',
    totalSamples: 6_362_620,
    fraudRatio: 0.00129,   // REAL: 8,213 / 6,362,620 = 0.129% (verified from PS_20174392719...log.csv)
    numFeatures: 11,
    fraudPattern: 'Balance-draining TRANSFER→CASH_OUT chains (step-based)',
    // Smooth convergence — mobile money fraud patterns are consistent/regular
    initialAuc: 0.8210,
    targetAuc: 0.9670,
    aucStepMean: 0.0145,
    aucStepStd: 0.0055,
    initialLoss: 0.4200,
    targetLoss: 0.0560,
    lossDecayRate: 0.034,
    bankSpreadStd: 0.012,
    championAucDefault: 0.7200,
  },

  ieee_cis: {
    id: 'ieee_cis',
    label: 'IEEE-CIS Fraud Detection',
    subtitle: 'Vesta Corp · 590K e-commerce txns · 394 features',
    badge: 'Real Production Data · Kaggle Competition',
    sourceLink: 'https://www.kaggle.com/competitions/ieee-fraud-detection',
    color: '#0ea5e9',   // sky blue — high-feature dense data
    icon: '🛒',
    totalSamples: 590_540,
    fraudRatio: 0.03499,
    numFeatures: 394,
    fraudPattern: 'Card-not-present e-commerce fraud, device fingerprinting',
    // High-feature count → faster convergence but more inter-bank variance
    initialAuc: 0.8920,
    targetAuc: 0.9810,
    aucStepMean: 0.0088,
    aucStepStd: 0.0042,
    initialLoss: 0.3500,
    targetLoss: 0.0420,
    lossDecayRate: 0.028,
    bankSpreadStd: 0.018,
    championAucDefault: 0.8200,
  },

  elliptic: {
    id: 'elliptic',
    label: 'Elliptic Bitcoin AML Graph',
    subtitle: '203K+ nodes · 234K+ directed edges · GNN topology',
    badge: 'Real On-Chain Graph Data · Elliptic Co.',
    sourceLink: 'https://www.kaggle.com/datasets/ellipticco/elliptic-data-set',
    color: '#f59e0b',   // amber — blockchain/graph context
    icon: '🔗',
    totalSamples: 46_564,       // REAL: labeled nodes only (unknown class excluded) from elliptic_txs_classes.csv
    fraudRatio: 0.09761,          // REAL: 4,545 illicit / 46,564 labeled nodes = 9.76%
    numFeatures: 166,
    fraudPattern: 'Illicit BTC entity clustering via graph topology + 49 time-steps',
    // GNN-style convergence: slower, noisier, but strong plateau
    initialAuc: 0.7820,
    targetAuc: 0.9510,
    aucStepMean: 0.0168,
    aucStepStd: 0.0092,
    initialLoss: 0.5200,
    targetLoss: 0.0890,
    lossDecayRate: 0.042,
    bankSpreadStd: 0.025,
    championAucDefault: 0.6900,
  },

  creditcard: {
    id: 'creditcard',
    label: 'European Credit Card Fraud',
    subtitle: '284K txns · 0.17% fraud · PCA V1–V28 features',
    badge: 'PCA Benchmark Standard · ULB Machine Learning Group',
    sourceLink: 'https://www.kaggle.com/datasets/mlg-ulb/creditcardfraud',
    color: '#10b981',   // emerald — classic benchmark
    icon: '💳',
    totalSamples: 284_807,
    fraudRatio: 0.001727,
    numFeatures: 30,
    fraudPattern: 'Extreme class imbalance (1:578) — requires SMOTE + focal loss',
    // Quick convergence with high variance due to class imbalance
    initialAuc: 0.8630,
    targetAuc: 0.9720,
    aucStepMean: 0.0108,
    aucStepStd: 0.0065,
    initialLoss: 0.4500,
    targetLoss: 0.0510,
    lossDecayRate: 0.038,
    bankSpreadStd: 0.016,
    championAucDefault: 0.7600,
  },
};

export const DATASET_IDS = Object.keys(DATASET_PROFILES) as DatasetProfile['id'][];
