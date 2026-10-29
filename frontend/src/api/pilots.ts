import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { apiClient } from './client';
import type {
  PilotApprovalRequest,
  PilotKillSwitchRequest,
  PilotPreflightRequest,
  PilotProvisionRequest,
  PilotStatusResponse,
  PilotTelemetrySummary,
} from './types';

export function usePilotsList() {
  return useQuery<PilotStatusResponse[], Error>({
    queryKey: ['pilots-list'],
    queryFn: async () => {
      const { data } = await apiClient.get<PilotStatusResponse[]>('/api/v1/pilots');
      return data;
    },
    staleTime: 5000,
    refetchInterval: 10000,
  });
}

export function usePilotStatus(pilotId: string | undefined) {
  return useQuery<PilotStatusResponse, Error>({
    queryKey: ['pilot-status', pilotId],
    queryFn: async () => {
      if (!pilotId) throw new Error('Pilot ID is required');
      const { data } = await apiClient.get<PilotStatusResponse>(`/api/v1/pilots/${pilotId}`);
      return data;
    },
    enabled: Boolean(pilotId),
    staleTime: 3000,
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      if (status === 'ACTIVE' || status === 'PREFLIGHT' || status === 'AWAITING_APPROVAL') {
        return 5000;
      }
      if (status === 'COMPLETED' || status === 'ABORTED' || status === 'FAILED') {
        return false;
      }
      return 10000;
    },
  });
}

export function usePilotTelemetry(pilotId: string | undefined, isTerminal = false) {
  return useQuery<PilotTelemetrySummary, Error>({
    queryKey: ['pilot-telemetry', pilotId],
    queryFn: async () => {
      if (!pilotId) throw new Error('Pilot ID is required');
      const { data } = await apiClient.get<PilotTelemetrySummary>(`/api/v1/pilots/${pilotId}/telemetry`);
      return data;
    },
    enabled: Boolean(pilotId),
    staleTime: 3000,
    refetchInterval: isTerminal ? false : 5000,
  });
}

export function usePilotDossier(pilotId: string | undefined, enabled = false) {
  return useQuery<Record<string, any>, Error>({
    queryKey: ['pilot-dossier', pilotId],
    queryFn: async () => {
      if (!pilotId) throw new Error('Pilot ID is required');
      const { data } = await apiClient.get<Record<string, any>>(`/api/v1/pilots/${pilotId}/dossier`);
      return data;
    },
    enabled: Boolean(pilotId) && enabled,
    staleTime: 30000,
  });
}

export function useProvisionPilot() {
  const queryClient = useQueryClient();
  return useMutation<PilotStatusResponse, Error, PilotProvisionRequest>({
    mutationFn: async (payload: PilotProvisionRequest) => {
      const { data } = await apiClient.post<PilotStatusResponse>('/api/v1/pilots', payload);
      return data;
    },
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: ['pilots-list'] });
      queryClient.setQueryData(['pilot-status', data.pilot_id], data);
    },
  });
}

export function useRunPilotPreflight() {
  const queryClient = useQueryClient();
  return useMutation<PilotStatusResponse, Error, { pilotId: string; payload?: PilotPreflightRequest }>({
    mutationFn: async ({ pilotId, payload }) => {
      const { data } = await apiClient.post<PilotStatusResponse>(
        `/api/v1/pilots/${pilotId}/preflight`,
        payload || {}
      );
      return data;
    },
    onSuccess: (data) => {
      queryClient.setQueryData(['pilot-status', data.pilot_id], data);
      queryClient.invalidateQueries({ queryKey: ['pilots-list'] });
    },
  });
}

export function useApprovePilot() {
  const queryClient = useQueryClient();
  return useMutation<PilotStatusResponse, Error, { pilotId: string; payload: PilotApprovalRequest }>({
    mutationFn: async ({ pilotId, payload }) => {
      const { data } = await apiClient.post<PilotStatusResponse>(
        `/api/v1/pilots/${pilotId}/approve`,
        payload
      );
      return data;
    },
    onSuccess: (data) => {
      queryClient.setQueryData(['pilot-status', data.pilot_id], data);
      queryClient.invalidateQueries({ queryKey: ['pilots-list'] });
    },
  });
}

export function useActivatePilot() {
  const queryClient = useQueryClient();
  return useMutation<PilotStatusResponse, Error, { pilotId: string }>({
    mutationFn: async ({ pilotId }) => {
      const { data } = await apiClient.post<PilotStatusResponse>(`/api/v1/pilots/${pilotId}/activate`);
      return data;
    },
    onSuccess: (data) => {
      queryClient.setQueryData(['pilot-status', data.pilot_id], data);
      queryClient.invalidateQueries({ queryKey: ['pilots-list'] });
      queryClient.invalidateQueries({ queryKey: ['pilot-telemetry', data.pilot_id] });
    },
  });
}

export function usePausePilot() {
  const queryClient = useQueryClient();
  return useMutation<PilotStatusResponse, Error, { pilotId: string; payload: PilotKillSwitchRequest }>({
    mutationFn: async ({ pilotId, payload }) => {
      const { data } = await apiClient.post<PilotStatusResponse>(
        `/api/v1/pilots/${pilotId}/pause`,
        payload
      );
      return data;
    },
    onSuccess: (data) => {
      queryClient.setQueryData(['pilot-status', data.pilot_id], data);
      queryClient.invalidateQueries({ queryKey: ['pilots-list'] });
      queryClient.invalidateQueries({ queryKey: ['pilot-telemetry', data.pilot_id] });
    },
  });
}

export function useResumePilot() {
  const queryClient = useQueryClient();
  return useMutation<PilotStatusResponse, Error, { pilotId: string }>({
    mutationFn: async ({ pilotId }) => {
      const { data } = await apiClient.post<PilotStatusResponse>(`/api/v1/pilots/${pilotId}/resume`);
      return data;
    },
    onSuccess: (data) => {
      queryClient.setQueryData(['pilot-status', data.pilot_id], data);
      queryClient.invalidateQueries({ queryKey: ['pilots-list'] });
      queryClient.invalidateQueries({ queryKey: ['pilot-telemetry', data.pilot_id] });
    },
  });
}

export function useAbortPilot() {
  const queryClient = useQueryClient();
  return useMutation<PilotStatusResponse, Error, { pilotId: string; payload: PilotKillSwitchRequest }>({
    mutationFn: async ({ pilotId, payload }) => {
      const { data } = await apiClient.post<PilotStatusResponse>(
        `/api/v1/pilots/${pilotId}/abort`,
        payload
      );
      return data;
    },
    onSuccess: (data) => {
      queryClient.setQueryData(['pilot-status', data.pilot_id], data);
      queryClient.invalidateQueries({ queryKey: ['pilots-list'] });
      queryClient.invalidateQueries({ queryKey: ['pilot-telemetry', data.pilot_id] });
    },
  });
}

export function useCompletePilot() {
  const queryClient = useQueryClient();
  return useMutation<PilotStatusResponse, Error, { pilotId: string }>({
    mutationFn: async ({ pilotId }) => {
      const { data } = await apiClient.post<PilotStatusResponse>(`/api/v1/pilots/${pilotId}/complete`);
      return data;
    },
    onSuccess: (data) => {
      queryClient.setQueryData(['pilot-status', data.pilot_id], data);
      queryClient.invalidateQueries({ queryKey: ['pilots-list'] });
      queryClient.invalidateQueries({ queryKey: ['pilot-telemetry', data.pilot_id] });
    },
  });
}
