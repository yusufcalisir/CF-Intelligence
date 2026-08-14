import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, waitFor, act } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import React from 'react';
import {
  useSimulations,
  useSimulation,
  useAlerts,
  useCases,
  useCase,
  useCreateCase,
  useCreateRule,
  useDeleteRule,
  useBankDistributions,
} from '../queries';
import { apiClient } from '../client';
import type { Case, CaseSummary, BusinessRule, Alert } from '../types';

const createWrapper = (queryClient: QueryClient) => {
  return ({ children }: { children: React.ReactNode }) =>
    React.createElement(QueryClientProvider, { client: queryClient }, children);
};

describe('State Management & React Query Lifecycle Tests', () => {
  let queryClient: QueryClient;

  beforeEach(() => {
    vi.restoreAllMocks();
    queryClient = new QueryClient({
      defaultOptions: {
        queries: {
          retry: false,
          gcTime: 1000 * 60 * 5,
        },
      },
    });
  });

  // ── 1. Loading, Fetching & Pending States ────────────────────────────────────
  describe('1. Loading & Fetching State Lifecycles', () => {
    it('tracks isLoading, isFetching and transitions to isSuccess when request resolves', async () => {
      let resolvePromise: (val: any) => void;
      const delayedPromise = new Promise((resolve) => {
        resolvePromise = resolve;
      });

      vi.spyOn(apiClient, 'get').mockReturnValueOnce(delayedPromise as any);

      const { result } = renderHook(() => useSimulations(), {
        wrapper: createWrapper(queryClient),
      });

      // Initial state: loading & fetching
      expect(result.current.isLoading).toBe(true);
      expect(result.current.isFetching).toBe(true);
      expect(result.current.data).toBeUndefined();

      // Resolve the async response
      act(() => {
        resolvePromise!({
          data: [
            {
              id: 'sim_active_01',
              status: 'running',
              current_round: 3,
              total_rounds: 10,
              progress_pct: 30,
              created_at: '2026-08-14T10:00:00Z',
              completed_at: null,
              duration_seconds: 45,
            },
          ],
        });
      });

      await waitFor(() => expect(result.current.isSuccess).toBe(true));
      expect(result.current.isLoading).toBe(false);
      expect(result.current.isFetching).toBe(false);
      expect(result.current.data).toHaveLength(1);
      expect(result.current.data?.[0]?.id).toBe('sim_active_01');
    });
  });

  // ── 2. Cache Retention & Stale Data Behavior ─────────────────────────────────
  describe('2. Cache Retention & Stale Data Handling', () => {
    it('serves cached data instantly on second hook mount without redundant network call within staleTime', async () => {
      const mockDist = {
        banks: {},
        divergence_summary: { amount_ks_statistic: {}, overall_non_iid_score: 0.42 },
      };

      const getSpy = vi.spyOn(apiClient, 'get').mockResolvedValue({ data: mockDist });

      // First mount: hits network
      const { result: firstResult, unmount } = renderHook(() => useBankDistributions(), {
        wrapper: createWrapper(queryClient),
      });
      await waitFor(() => expect(firstResult.current.isSuccess).toBe(true));
      expect(getSpy).toHaveBeenCalledTimes(1);

      unmount();

      // Second mount within staleTime: serves from cache instantly
      const { result: secondResult } = renderHook(() => useBankDistributions(), {
        wrapper: createWrapper(queryClient),
      });

      expect(secondResult.current.data).toEqual(mockDist);
      expect(secondResult.current.isSuccess).toBe(true);
      // Network spy should NOT have been called a second time
      expect(getSpy).toHaveBeenCalledTimes(1);
    });

    it('deduplicates simultaneous concurrent queries for the same queryKey', async () => {
      const mockCases: CaseSummary[] = [
        {
          id: 'CASE-CONCURRENT',
          title: 'Concurrent Test Case',
          status: 'open',
          priority: 'p2_high',
          assigned_to: 'analyst_01',
          alert_count: 1,
          created_at: '2026-08-14T10:00:00Z',
          is_open: true,
        },
      ];
      const getSpy = vi.spyOn(apiClient, 'get').mockResolvedValue({ data: mockCases });

      // Mount two hooks concurrently pointing to the same queryKey ['cases', undefined]
      const { result: hookA } = renderHook(() => useCases(), {
        wrapper: createWrapper(queryClient),
      });
      const { result: hookB } = renderHook(() => useCases(), {
        wrapper: createWrapper(queryClient),
      });

      await waitFor(() => {
        expect(hookA.current.isSuccess).toBe(true);
        expect(hookB.current.isSuccess).toBe(true);
      });

      expect(hookA.current.data).toEqual(mockCases);
      expect(hookB.current.data).toEqual(mockCases);
      // Both hooks shared the exact same network flight
      expect(getSpy).toHaveBeenCalledTimes(1);
    });
  });

  // ── 3. Error Handling & Retry Policies ───────────────────────────────────────
  describe('3. Error Handling & Retry Policy Invariants', () => {
    it('captures error state and exposes error message on network failure', async () => {
      const networkError = new Error('503 Service Unavailable: Gateway Offline');
      vi.spyOn(apiClient, 'get').mockRejectedValue(networkError);

      const { result } = renderHook(() => useCase('case_fail_01'), {
        wrapper: createWrapper(queryClient),
      });

      await waitFor(() => expect(result.current.isError).toBe(true));
      expect(result.current.error?.message).toContain('503 Service Unavailable');
      expect(result.current.data).toBeUndefined();
    });

    it('does not retry when receiving HTTP 404 Not Found error', async () => {
      const notFoundError = { response: { status: 404, data: { detail: 'Simulation not found' } } };
      const getSpy = vi.spyOn(apiClient, 'get').mockRejectedValue(notFoundError);

      const { result } = renderHook(() => useSimulation('sim_nonexistent'), {
        wrapper: createWrapper(queryClient),
      });

      await waitFor(() => expect(result.current.isError).toBe(true));
      // Retry policy should stop immediately on 404
      expect(getSpy).toHaveBeenCalledTimes(1);
    });
  });

  // ── 4. Cache Invalidation on Mutations ───────────────────────────────────────
  describe('4. Automated Cache Invalidation on Mutations', () => {
    it('invalidates ["cases"] and ["dashboard-stats"] queries when useCreateCase succeeds', async () => {
      const initialCases: CaseSummary[] = [
        {
          id: 'CASE-001',
          title: 'Initial Case',
          status: 'open',
          priority: 'p3_medium',
          assigned_to: null,
          alert_count: 0,
          created_at: '2026-08-14T08:00:00Z',
          is_open: true,
        },
      ];

      const newCase: Case = {
        id: 'CASE-002',
        title: 'New Investigation Case',
        status: 'open',
        priority: 'p1_critical',
        assigned_to: null,
        alert_ids: ['ALT-001'],
        evidence_ids: [],
        notes: [],
        timeline: [],
        created_at: '2026-08-14T10:00:00Z',
        updated_at: null,
        closed_at: null,
        total_risk_score: 95.0,
        duration_hours: null,
        is_open: true,
      };

      vi.spyOn(apiClient, 'get').mockResolvedValue({ data: initialCases });
      vi.spyOn(apiClient, 'post').mockResolvedValueOnce({ data: newCase });

      // Pre-populate queries cache
      queryClient.setQueryData(['cases', undefined], initialCases);
      const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries');

      const { result } = renderHook(() => useCreateCase(), {
        wrapper: createWrapper(queryClient),
      });

      await act(async () => {
        await result.current.mutateAsync({
          title: 'New Investigation Case',
          priority: 'p1_critical',
          alert_ids: ['ALT-001'],
        });
      });

      expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['cases'] });
      expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['dashboard-stats'] });
    });

    it('invalidates ["business-rules"] cache when useCreateRule and useDeleteRule succeed', async () => {
      const mockRule: BusinessRule = {
        id: 'rule_99',
        rule_name: 'Block Structuring',
        condition: { field: 'amount', operator: '>=', value: 10000 },
        action: 'BLOCK',
        is_active: true,
        created_at: '2026-08-14T10:00:00Z',
      };

      vi.spyOn(apiClient, 'post').mockResolvedValueOnce({ data: mockRule });
      vi.spyOn(apiClient, 'delete').mockResolvedValueOnce({ data: { success: true } });
      const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries');

      const { result: createHook } = renderHook(() => useCreateRule(), {
        wrapper: createWrapper(queryClient),
      });

      await act(async () => {
        await createHook.current.mutateAsync({
          rule_name: 'Block Structuring',
          condition: { field: 'amount', operator: '>=', value: 10000 },
          action: 'BLOCK',
          is_active: true,
        });
      });

      expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['business-rules'] });

      const { result: deleteHook } = renderHook(() => useDeleteRule(), {
        wrapper: createWrapper(queryClient),
      });

      await act(async () => {
        await deleteHook.current.mutateAsync('rule_99');
      });

      expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['business-rules'] });
    });
  });

  // ── 5. Optimistic Updates & Error Rollback ────────────────────────────────────
  describe('5. Optimistic UI Updates & Error Rollback Strategy', () => {
    it('applies optimistic update instantly and rolls back to previous snapshot on mutation rejection', async () => {
      const previousCases: CaseSummary[] = [
        {
          id: 'CASE-001',
          title: 'Stable Case',
          status: 'open',
          priority: 'p3_medium',
          assigned_to: 'analyst_01',
          alert_count: 2,
          created_at: '2026-08-14T08:00:00Z',
          is_open: true,
        },
      ];

      // Set baseline query cache
      queryClient.setQueryData(['cases', undefined], previousCases);

      // Simulation of an optimistic mutation handler
      const optimisticUpdateFn = async (newCasePayload: CaseSummary, shouldFail: boolean) => {
        // 1. Cancel ongoing refetches
        await queryClient.cancelQueries({ queryKey: ['cases', undefined] });

        // 2. Snapshot previous state
        const previousSnapshot = queryClient.getQueryData<CaseSummary[]>(['cases', undefined]);

        // 3. Optimistically update query client state
        queryClient.setQueryData<CaseSummary[]>(['cases', undefined], (old = []) => [
          ...old,
          newCasePayload,
        ]);

        try {
          if (shouldFail) {
            throw new Error('500 Internal Server Error: Database deadlock');
          }
          return newCasePayload;
        } catch (err) {
          // 4. Rollback to snapshot on failure
          queryClient.setQueryData(['cases', undefined], previousSnapshot);
          throw err;
        }
      };

      const optimisticCase: CaseSummary = {
        id: 'CASE-OPTIMISTIC-TEMP',
        title: 'Optimistic High Risk Mule Alert',
        status: 'open',
        priority: 'p1_critical',
        assigned_to: 'lead_analyst',
        alert_count: 5,
        created_at: '2026-08-14T10:00:00Z',
        is_open: true,
      };

      // Test Successful Optimistic Application
      await optimisticUpdateFn(optimisticCase, false);
      let cachedCases = queryClient.getQueryData<CaseSummary[]>(['cases', undefined]);
      expect(cachedCases).toHaveLength(2);
      expect(cachedCases?.map((c) => c.id)).toContain('CASE-OPTIMISTIC-TEMP');

      // Test Failed Optimistic Mutation & Deterministic Rollback
      const failingCase: CaseSummary = {
        id: 'CASE-FAILING-TEMP',
        title: 'Failing Case Attempt',
        status: 'open',
        priority: 'p2_high',
        assigned_to: null,
        alert_count: 1,
        created_at: '2026-08-14T10:05:00Z',
        is_open: true,
      };

      await expect(optimisticUpdateFn(failingCase, true)).rejects.toThrow('500 Internal Server Error');

      // Verify that cache rolled back to before the failed attempt
      cachedCases = queryClient.getQueryData<CaseSummary[]>(['cases', undefined]);
      expect(cachedCases).toHaveLength(2);
      expect(cachedCases?.map((c) => c.id)).not.toContain('CASE-FAILING-TEMP');
    });
  });

  // ── 6. Pagination & Filter Key Isolation ─────────────────────────────────────
  describe('6. Pagination, Filters & Query Key Isolation', () => {
    it('isolates cache per filter parameters and refetches on parameter changes', async () => {
      const bankAAlerts: Alert[] = [
        {
          id: 'ALT-A-01',
          bank_id: 'bank_a',
          transaction_id: 'TXN-A1',
          risk_score: 85,
          severity: 'critical',
          status: 'NEW',
          reason_codes: ['STRUCTURING'],
          confidence: 0.9,
          involved_entity_ids: [],
          created_at: '2026-08-14T10:00:00Z',
          top_features: [],
          risk_factors: [],
          model_confidence: 0.9,
        },
      ];

      const bankBAlerts: Alert[] = [
        {
          id: 'ALT-B-01',
          bank_id: 'bank_b',
          transaction_id: 'TXN-B1',
          risk_score: 72,
          severity: 'high',
          status: 'NEW',
          reason_codes: ['HIGH_VELOCITY'],
          confidence: 0.88,
          involved_entity_ids: [],
          created_at: '2026-08-14T10:00:00Z',
          top_features: [],
          risk_factors: [],
          model_confidence: 0.88,
        },
      ];

      const getSpy = vi.spyOn(apiClient, 'get').mockImplementation(async (_url, config) => {
        const params = config?.params as Record<string, any> | undefined;
        if (params?.bank_id === 'bank_a') return { data: bankAAlerts };
        if (params?.bank_id === 'bank_b') return { data: bankBAlerts };
        return { data: [] };
      });

      // Filter by Bank A
      const { result, rerender } = renderHook(
        (props: { bank_id: string }) => useAlerts({ bank_id: props.bank_id }),
        {
          wrapper: createWrapper(queryClient),
          initialProps: { bank_id: 'bank_a' },
        }
      );

      await waitFor(() => expect(result.current.isSuccess).toBe(true));
      expect(result.current.data?.[0]?.id).toBe('ALT-A-01');

      // Switch filter to Bank B
      rerender({ bank_id: 'bank_b' });
      await waitFor(() => expect(result.current.data?.[0]?.id).toBe('ALT-B-01'));

      expect(getSpy).toHaveBeenCalledTimes(2);

      // Verify Query Cache holds both independent filter entries
      const cacheKeys = queryClient.getQueryCache().getAll().map((q) => q.queryKey);
      expect(cacheKeys).toContainEqual(['alerts', { bank_id: 'bank_a' }]);
      expect(cacheKeys).toContainEqual(['alerts', { bank_id: 'bank_b' }]);
    });
  });
});
