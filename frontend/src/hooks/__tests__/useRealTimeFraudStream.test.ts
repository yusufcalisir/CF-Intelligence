import { describe, it, expect, beforeEach } from 'vitest';
import { useLiveAlertStore } from '../../stores/useLiveAlertStore';

describe('useLiveAlertStore Unit Tests', () => {
  beforeEach(() => {
    useLiveAlertStore.setState({
      status: 'connecting',
      latencyMs: 3.2,
      totalStreamedTransactions: 100,
      recentTransactions: [],
      activeAlertToasts: [],
    });
  });

  it('updates connection status and latency', () => {
    const store = useLiveAlertStore.getState();
    store.setStatus('connected');
    store.setLatencyMs(2.4);

    expect(useLiveAlertStore.getState().status).toBe('connected');
    expect(useLiveAlertStore.getState().latencyMs).toBe(2.4);
  });

  it('pushes normal transaction without generating toast', () => {
    const store = useLiveAlertStore.getState();
    store.pushStreamEvent({
      transaction_id: 'txn_001',
      bank_id: 'bank_alpha',
      amount: 50.0,
      currency: 'EUR',
      risk_score: 120,
      severity: 'info',
      typology: 'LEGITIMATE_PAYMENT',
      description: 'Standard retail payment',
      created_at: new Date().toISOString(),
    });

    const state = useLiveAlertStore.getState();
    expect(state.totalStreamedTransactions).toBe(101);
    expect(state.recentTransactions.length).toBe(1);
    expect(state.activeAlertToasts.length).toBe(0);
  });

  it('pushes high-risk transaction and automatically creates alert toast', () => {
    const store = useLiveAlertStore.getState();
    store.pushStreamEvent({
      transaction_id: 'txn_critical_99',
      bank_id: 'bank_alpha',
      amount: 250000.0,
      currency: 'EUR',
      risk_score: 942,
      severity: 'critical',
      typology: 'RAPID_CROSS_BANK_LAYERING',
      description: 'High velocity transfer burst',
      created_at: new Date().toISOString(),
    });

    const state = useLiveAlertStore.getState();
    expect(state.totalStreamedTransactions).toBe(101);
    expect(state.activeAlertToasts.length).toBe(1);
    const firstToast = state.activeAlertToasts[0]!;
    expect(firstToast.risk_score).toBe(942);
    expect(firstToast.severity).toBe('critical');

    // Dismiss toast
    const toastId = firstToast.id;
    store.dismissToast(toastId);
    expect(useLiveAlertStore.getState().activeAlertToasts.length).toBe(0);

  });
});
