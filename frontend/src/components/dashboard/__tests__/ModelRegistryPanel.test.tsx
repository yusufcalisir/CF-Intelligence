import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import ModelRegistryPanel from '../ModelRegistryPanel';
import * as queries from '../../../api/queries';
import type { ModelVersion } from '../../../api/types';

describe('ModelRegistryPanel Component (User Interaction)', () => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });

  const mockVersions: ModelVersion[] = [
    {
      version: 2,
      filename: 'model_v2.pt',
      is_active: true,
      created_at: '2026-08-14T10:00:00Z',
      status: 'ACTIVE_CHAMPION',
      git_commit_hash: 'a1b2c3d4',
      dataset_hash: 'd4e5f6g7',
      dp_noise_profile: {
        mechanism: 'opacus',
        epsilon: 1.25,
        delta: 1e-5,
      },
      metrics: {
        accuracy: 0.952,
        precision: 0.91,
        recall: 0.875,
        f1_score: 0.892,
        auc_roc: 0.945,
        loss: 0.125,
      },
      sign_offs: [],
    },
    {
      version: 1,
      filename: 'model_v1.pt',
      is_active: false,
      created_at: '2026-08-14T09:00:00Z',
      status: 'DEPRECATED',
      git_commit_hash: 'f7e6d5c4',
      dataset_hash: 'c3b2a100',
      dp_noise_profile: {
        mechanism: 'gaussian',
        epsilon: 2.0,
        delta: 1e-5,
      },
      metrics: {
        accuracy: 0.92,
        precision: 0.88,
        recall: 0.85,
        f1_score: 0.865,
        auc_roc: 0.912,
        loss: 0.21,
      },
      sign_offs: [],
    },
  ];

  beforeEach(() => {
    vi.spyOn(queries, 'useModelVersions').mockReturnValue({
      data: mockVersions,
      isLoading: false,
      refetch: vi.fn(),
    } as any);

    vi.spyOn(queries, 'useCanaryHistory').mockReturnValue({
      data: [],
      isLoading: false,
    } as any);

    vi.spyOn(queries, 'useShadowMetrics').mockReturnValue({
      data: null,
      refetch: vi.fn(),
    } as any);

    vi.spyOn(queries, 'useRollbackModel').mockReturnValue({
      mutateAsync: vi.fn().mockResolvedValue({ success: true }),
    } as any);

    vi.spyOn(queries, 'useSignOffModel').mockReturnValue({
      mutateAsync: vi.fn().mockResolvedValue({ success: true }),
    } as any);

    vi.spyOn(queries, 'useSubmitFeedback').mockReturnValue({
      mutateAsync: vi.fn().mockResolvedValue({ success: true }),
    } as any);
  });

  it('renders model versions, active champion badge, and tab navigation', () => {
    render(
      <QueryClientProvider client={queryClient}>
        <ModelRegistryPanel simulationId="sim_test" />
      </QueryClientProvider>
    );

    expect(screen.getByText(/Model Registry & Canary Evaluation/i)).toBeInTheDocument();
    expect(screen.getByText(/Active Production Model/i)).toBeInTheDocument();
    expect(screen.getAllByText('0.9450').length).toBeGreaterThan(0);
  });

  it('allows user to switch tabs between Registry, Canary and Shadow models', async () => {
    const user = userEvent.setup();

    render(
      <QueryClientProvider client={queryClient}>
        <ModelRegistryPanel simulationId="sim_test" />
      </QueryClientProvider>
    );

    const canaryTab = screen.getByRole('button', { name: /Canary Decision Logs/i });
    await user.click(canaryTab);

    expect(screen.getByText(/Canary Gate Rule/i)).toBeInTheDocument();
  });
});
