import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import ComplianceReportPanel from '../ComplianceReportPanel';
import * as queries from '../../../api/queries';

describe('ComplianceReportPanel Component', () => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });

  beforeEach(() => {
    vi.spyOn(queries, 'useAIActComplianceReport').mockReturnValue({
      data: {
        article_10_governance: { status: 'COMPLIANT', details: 'Zero raw PII decentralized training' },
        article_13_transparency: { status: 'COMPLIANT', details: 'Cryptographic hash audit logs verified' },
        fairness_evaluation: { disparate_impact_ratio: 0.95, equal_opportunity_difference: 0.02 },
      },
      isLoading: false,
      error: null,
    } as any);
  });

  it('renders EU AI Act compliance metrics, fairness audits, and status badges', () => {
    const mockBanks = [
      {
        bank_id: 'bank_a',
        local_metrics: {} as any,
        federated_metrics: {
          accuracy: 0.95,
          precision: 0.92,
          recall: 0.89,
          f1: 0.905,
          auc: 0.96,
          loss: 0.12,
          disparate_impact: 0.95,
          equal_opportunity_diff: 0.02,
          protected_selection_rate: 0.048,
          reference_selection_rate: 0.050,
        },
      },
    ];

    render(
      <QueryClientProvider client={queryClient}>
        <ComplianceReportPanel simulationId="sim_test" banks={mockBanks} />
      </QueryClientProvider>
    );

    expect(screen.getByText(/AI Regulatory Compliance & Bias Audit/i)).toBeInTheDocument();
    expect(screen.getByText(/EU AI Act Article 10 & 13 audit log/i)).toBeInTheDocument();
    expect(screen.getByText(/COMPLIANT/i)).toBeInTheDocument();
  });
});
