import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import React from 'react';
import { useSimulations, useDashboardStats, useSecurityStatus } from '../queries';
import { apiClient } from '../client';

const createWrapper = () => {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
    },
  });
  return ({ children }: { children: React.ReactNode }) =>
    React.createElement(QueryClientProvider, { client: queryClient }, children);
};

describe('api/queries hooks', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('useSimulations returns simulation data on success', async () => {
    const mockSimulations = [
      {
        id: 'sim_001',
        name: 'FL Test Run',
        status: 'completed',
        current_round: 10,
        total_rounds: 10,
        created_at: '2026-08-14T10:00:00Z',
      },
    ];
    vi.spyOn(apiClient, 'get').mockResolvedValueOnce({ data: mockSimulations });

    const { result } = renderHook(() => useSimulations(), { wrapper: createWrapper() });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toEqual(mockSimulations);
  });

  it('useDashboardStats returns dashboard KPI structure on success', async () => {
    const mockStats = {
      total_alerts: 42,
      critical_alerts: 5,
      open_cases: 8,
      total_entities: 120,
      active_banks: 4,
    };
    vi.spyOn(apiClient, 'get').mockResolvedValueOnce({ data: mockStats });

    const { result } = renderHook(() => useDashboardStats(), { wrapper: createWrapper() });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toEqual(mockStats);
  });

  it('useSecurityStatus returns security posture indicators on success', async () => {
    const mockSecurity = {
      mtls: { enabled: true, ca_cn: 'CFI Root CA', tls_version: '1.3', peer_verification: 'STRICT' },
      pqc_enabled: true,
      vault_hsm_status: 'ONLINE',
    };
    vi.spyOn(apiClient, 'get').mockResolvedValueOnce({ data: mockSecurity });

    const { result } = renderHook(() => useSecurityStatus(), { wrapper: createWrapper() });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toEqual(mockSecurity);
  });
});
