import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import PrivacyMonitor from '../PrivacyMonitor';
import { AdversarialDefensePanel } from '../AdversarialDefensePanel';
import BankCard from '../BankCard';
import ConfusionMatrix from '../../charts/ConfusionMatrix';
import LossChart from '../../charts/LossChart';
import { createMockSimulationDetail, createMockTrainingRounds } from '../../../testing/factories/simulationFactory';
import { createMockBank } from '../../../testing/factories/bankFactory';

describe('Frontend Business Logic & Component Branch Coverage Suite', () => {
  describe('PrivacyMonitor Component Branches', () => {
    it('returns null when differential privacy is disabled in config', () => {
      const simNoDp = createMockSimulationDetail();
      simNoDp.config.privacy_mechanism = 'secure_aggregation';

      const { container } = render(
        <PrivacyMonitor simulation={simNoDp} rounds={[]} />
      );
      expect(container.firstChild).toBeNull();
    });

    it('renders Opacus DP metrics when differential privacy is enabled', () => {
      const simDp = createMockSimulationDetail({
        current_round: 3,
      });
      simDp.config.privacy_mechanism = 'differential_privacy';
      simDp.config.dp_mode = 'opacus';
      simDp.config.dp_epsilon = 4.0;
      simDp.config.dp_delta = 1e-5;

      const rounds = createMockTrainingRounds(3);

      render(<PrivacyMonitor simulation={simDp} rounds={rounds} />);
      expect(screen.getByText(/Differential Privacy \(DP\) Monitor/i)).toBeInTheDocument();
      expect(screen.getByText(/Opacus \(Moments Accountant\)/i)).toBeInTheDocument();
    });
  });

  describe('AdversarialDefensePanel Component Branches', () => {
    it('renders defense configuration cards and toggles active status branches', () => {
      const { rerender } = render(<AdversarialDefensePanel isEnabled={false} />);
      expect(screen.getByText(/BASELINE \(NO ADV DEFENSE\)/i)).toBeInTheDocument();

      rerender(
        <AdversarialDefensePanel
          isEnabled={true}
          metrics={{
            accuracy: 0.96,
            clean_accuracy: 0.95,
            robust_accuracy: 0.89,
            fgsm_evasion_rate: 0.03,
            pgd_evasion_rate: 0.05,
            adversarial_robustness_score: 0.91,
          } as any}
        />
      );
      expect(screen.getByText(/Active Defense & Adversarial ML Training/i)).toBeInTheDocument();
    });
  });

  describe('BankCard Component Branches', () => {
    it('renders bank card with active and custom status branches', () => {
      const activeBankInfo = {
        id: 'bank_a',
        name: 'Meridian National',
        tier: 'Tier 1 Global',
        description: 'Large multinational bank',
        default_fraud_ratio: 0.015,
        default_transactions: 250000,
        fraud_pattern: 'Cross-border wire layering and smurfing',
        characteristics: ['High Volume', 'Cross-Border FX', 'Retail'],
      };

      const { rerender } = render(
        <BankCard bank={activeBankInfo} index={0} />
      );
      expect(screen.getByText('Meridian National')).toBeInTheDocument();
      expect(screen.getByText(/Tier 1 Global bank/i)).toBeInTheDocument();
      expect(screen.getByText(/Cross-border wire layering and smurfing/i)).toBeInTheDocument();

      // Rerender with custom bank (Nexus Digital - bank_b logo branch)
      const bankCustom = {
        ...activeBankInfo,
        id: 'bank_b',
        name: 'Nexus Digital',
        tier: 'Tier 2 Regional',
        characteristics: ['Commercial', 'Crypto-Onramp'],
      };
      rerender(<BankCard bank={bankCustom} index={1} />);
      expect(screen.getByText('Nexus Digital')).toBeInTheDocument();
      expect(screen.getByText(/Tier 2 Regional bank/i)).toBeInTheDocument();

      // Rerender with third bank (Heritage Regional - bank_c logo branch)
      const bankC = {
        ...activeBankInfo,
        id: 'bank_c',
        name: 'Heritage Regional',
        tier: 'Community',
      };
      rerender(<BankCard bank={bankC} index={2} />);
      expect(screen.getByText('Heritage Regional')).toBeInTheDocument();
    });
  });

  describe('Charts Branch Coverage (ConfusionMatrix & LossChart)', () => {
    it('renders ConfusionMatrix with local and federated metrics branches', () => {
      const mockBank = createMockBank('bank_a', {
        local_metrics: {
          accuracy: 0.89,
          precision: 0.85,
          recall: 0.82,
          f1: 0.83,
          auc_roc: 0.91,
          confusion_matrix: [
            [9110, 15],
            [25, 850],
          ],
        },
        federated_metrics: {
          accuracy: 0.95,
          precision: 0.93,
          recall: 0.91,
          f1: 0.92,
          auc_roc: 0.97,
          confusion_matrix: [
            [9062, 8],
            [10, 920],
          ],
        },
      } as any);

      const { rerender, container } = render(
        <ConfusionMatrix bank={mockBank as any} modelType="local" />
      );
      expect(screen.getByText(/Local/i)).toBeInTheDocument();
      expect(screen.getByText('TN')).toBeInTheDocument();
      expect(screen.getByText('FP')).toBeInTheDocument();

      rerender(<ConfusionMatrix bank={mockBank as any} modelType="federated" />);
      expect(screen.getByText(/Federated/i)).toBeInTheDocument();

      // Null metrics branch
      const bankNoMetrics = { ...mockBank, local_metrics: null as any };
      rerender(<ConfusionMatrix bank={bankNoMetrics as any} modelType="local" />);
      expect(container.firstChild).toBeNull();
    });

    it('renders LossChart with empty history and round history branches', () => {
      const { rerender } = render(<LossChart rounds={[]} />);
      expect(screen.getByText(/Training Loss Convergence/i)).toBeInTheDocument();
      expect(screen.getByText(/Waiting for Round 1/i)).toBeInTheDocument();

      const sampleRounds = createMockTrainingRounds(2);
      rerender(<LossChart rounds={sampleRounds} />);
      expect(screen.getByText(/Training Loss Convergence/i)).toBeInTheDocument();
    });
  });
});
