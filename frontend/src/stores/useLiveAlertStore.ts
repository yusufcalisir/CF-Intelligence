import { create } from 'zustand';

export type WebSocketConnectionStatus = 'connected' | 'connecting' | 'disconnected' | 'mock_active';
export type StreamSourceType = 'LIVE_CONNECTOR' | 'SIMULATED' | 'REPLAY' | 'UNAVAILABLE';

export interface LiveStreamTransaction {
  transaction_id: string;
  bank_id: string;
  amount: number;
  currency: string;
  risk_score: number;
  severity: 'critical' | 'high' | 'medium' | 'low' | 'info';
  typology: string;
  description: string;
  created_at: string;
  provenance?: string;
}

export interface LiveAlertToast {
  id: string;
  transaction_id: string;
  bank_id: string;
  risk_score: number;
  severity: 'critical' | 'high' | 'medium' | 'low' | 'info';
  typology: string;
  description: string;
  amount: number;
  currency: string;
  created_at: string;
  provenance?: string;
}

interface LiveAlertStoreState {
  status: WebSocketConnectionStatus;
  streamSource: StreamSourceType;
  streamProvenance: string;
  latencyMs: number;
  totalStreamedTransactions: number;
  recentTransactions: LiveStreamTransaction[];
  activeAlertToasts: LiveAlertToast[];
  setStatus: (status: WebSocketConnectionStatus) => void;
  setStreamSource: (source: StreamSourceType, provenance?: string) => void;
  setLatencyMs: (latencyMs: number) => void;
  pushStreamEvent: (txn: LiveStreamTransaction) => void;
  dismissToast: (id: string) => void;
  clearAllToasts: () => void;
}

export const useLiveAlertStore = create<LiveAlertStoreState>((set) => ({
  status: 'connecting',
  streamSource: 'UNAVAILABLE',
  streamProvenance: 'INITIALIZING',
  latencyMs: 0.0,
  totalStreamedTransactions: 0,
  recentTransactions: [],
  activeAlertToasts: [],

  setStatus: (status) => set({ status }),
  setStreamSource: (source, provenance = '') => set({ streamSource: source, streamProvenance: provenance }),
  setLatencyMs: (latencyMs) => set({ latencyMs }),

  pushStreamEvent: (txn) =>
    set((state) => {
      const isHighRisk = txn.risk_score >= 700 || txn.severity === 'critical' || txn.severity === 'high';
      const newToast: LiveAlertToast | null = isHighRisk
        ? {
            id: `toast_${Date.now()}_${Math.random().toString(36).substring(2, 6)}`,
            ...txn,
          }
        : null;

      const updatedToasts = newToast
        ? [newToast, ...state.activeAlertToasts].slice(0, 3) // Max 3 concurrent floating toasts
        : state.activeAlertToasts;

      return {
        totalStreamedTransactions: state.totalStreamedTransactions + 1,
        recentTransactions: [txn, ...state.recentTransactions].slice(0, 20),
        activeAlertToasts: updatedToasts,
      };
    }),

  dismissToast: (id) =>
    set((state) => ({
      activeAlertToasts: state.activeAlertToasts.filter((t) => t.id !== id),
    })),

  clearAllToasts: () => set({ activeAlertToasts: [] }),
}));

