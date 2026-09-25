import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useLiveAlertStore } from '../../stores/useLiveAlertStore';
import { useRealTimeFraudStream } from '../useRealTimeFraudStream';

class MockWebSocket {
  static instances: MockWebSocket[] = [];
  static originalWebSocket = global.WebSocket;

  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSING = 2;
  static readonly CLOSED = 3;

  readonly CONNECTING = 0;
  readonly OPEN = 1;
  readonly CLOSING = 2;
  readonly CLOSED = 3;

  url: string;
  readyState: number = 0; // 0: CONNECTING, 1: OPEN, 2: CLOSING, 3: CLOSED
  onopen: ((ev: any) => void) | null = null;
  onclose: ((ev: any) => void) | null = null;
  onmessage: ((ev: any) => void) | null = null;
  onerror: ((ev: any) => void) | null = null;
  sentMessages: string[] = [];

  constructor(url: string) {
    this.url = url;
    MockWebSocket.instances.push(this);
  }

  send(data: string) {
    this.sentMessages.push(data);
  }

  close(code?: number, reason?: string) {
    this.readyState = 3;
    if (this.onclose) {
      this.onclose({ code: code ?? 1000, reason: reason ?? 'Closed' });
    }
  }

  simulateOpen() {
    this.readyState = 1;
    if (this.onopen) {
      this.onopen({});
    }
  }

  simulateMessage(data: any) {
    if (this.onmessage) {
      this.onmessage({ data: typeof data === 'string' ? data : JSON.stringify(data) });
    }
  }

  simulateClose(code = 1006) {
    this.readyState = 3;
    if (this.onclose) {
      this.onclose({ code, reason: 'Abnormal Closure' });
    }
  }
}

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

describe('useRealTimeFraudStream Lifecycle & Reconnection Invariants', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    MockWebSocket.instances = [];
    (globalThis as any).WebSocket = MockWebSocket;
    (window as any).WebSocket = MockWebSocket;
    useLiveAlertStore.setState({
      status: 'connecting',
      latencyMs: 3.2,
      totalStreamedTransactions: 100,
      recentTransactions: [],
      activeAlertToasts: [],
    });
  });

  afterEach(() => {
    vi.useRealTimers();
    (globalThis as any).WebSocket = MockWebSocket.originalWebSocket;
    (window as any).WebSocket = MockWebSocket.originalWebSocket;
  });

  it('establishes connection and sends ping probe for real RTT latency', () => {
    const { unmount } = renderHook(() => useRealTimeFraudStream());

    expect(MockWebSocket.instances.length).toBe(1);
    const ws = MockWebSocket.instances[0]!;

    act(() => {
      ws.simulateOpen();
    });

    expect(useLiveAlertStore.getState().status).toBe('connected');
    expect(ws.sentMessages.length).toBeGreaterThan(0);
    expect(ws.sentMessages[0]).toContain('"type":"ping"');

    // Simulate PONG from backend
    act(() => {
      ws.simulateMessage({ event_type: 'PONG', timestamp: Date.now() });
    });

    expect(useLiveAlertStore.getState().latencyMs).toBeGreaterThan(0);
    unmount();
  });

  it('does NOT abandon reconnect even after multiple disconnects (eliminates dead-end bug)', () => {
    const { unmount } = renderHook(() => useRealTimeFraudStream());

    expect(MockWebSocket.instances.length).toBe(1);
    const ws1 = MockWebSocket.instances[0]!;

    // First disconnect
    act(() => {
      ws1.simulateClose();
    });
    expect(useLiveAlertStore.getState().status).toBe('disconnected');

    // Advance timer to trigger reconnect #1
    act(() => {
      vi.advanceTimersByTime(2500);
    });
    expect(MockWebSocket.instances.length).toBe(2);
    const ws2 = MockWebSocket.instances[1]!;

    // Second disconnect
    act(() => {
      ws2.simulateClose();
    });

    // Advance timer to trigger reconnect #2
    act(() => {
      vi.advanceTimersByTime(4000);
    });
    expect(MockWebSocket.instances.length).toBe(3);
    const ws3 = MockWebSocket.instances[2]!;

    // Third disconnect (Previously this abandoned reconnect permanently!)
    act(() => {
      ws3.simulateClose();
    });

    // Advance timer to trigger reconnect #3 (Must NOT be abandoned)
    act(() => {
      vi.advanceTimersByTime(7000);
    });
    expect(MockWebSocket.instances.length).toBe(4);
    const ws4 = MockWebSocket.instances[3]!;

    // Fourth disconnect
    act(() => {
      ws4.simulateClose();
    });

    // Advance timer to trigger reconnect #4
    act(() => {
      vi.advanceTimersByTime(12000);
    });
    expect(MockWebSocket.instances.length).toBe(5);

    // Reconnection succeeds on ws5
    const ws5 = MockWebSocket.instances[4]!;
    act(() => {
      ws5.simulateOpen();
    });
    expect(useLiveAlertStore.getState().status).toBe('connected');

    unmount();
  });

  it('parses real stream transactions and high-risk alerts from WebSocket', () => {
    const { unmount } = renderHook(() => useRealTimeFraudStream());
    const ws = MockWebSocket.instances[0]!;

    act(() => {
      ws.simulateOpen();
    });

    act(() => {
      ws.simulateMessage({
        event_type: 'ALERT_TRIGGERED',
        payload: {
          transaction_id: 'txn_live_real_001',
          bank_id: 'bank_beta',
          amount: 85000.0,
          currency: 'EUR',
          risk_score: 875,
          severity: 'critical',
          typology: 'GNN_TOPOLOGICAL_ANOMALY',
          description: 'Multi-hop syndicate layer detected',
          created_at: new Date().toISOString(),
        },
      });
    });

    const state = useLiveAlertStore.getState();
    expect(state.totalStreamedTransactions).toBe(101);
    expect(state.recentTransactions[0]?.transaction_id).toBe('txn_live_real_001');
    expect(state.activeAlertToasts.length).toBe(1);
    expect(state.activeAlertToasts[0]?.risk_score).toBe(875);

    unmount();
  });

  it('cleans up sockets and timers cleanly on unmount', () => {
    const { unmount } = renderHook(() => useRealTimeFraudStream());
    const ws = MockWebSocket.instances[0]!;

    act(() => {
      ws.simulateOpen();
    });

    unmount();
    expect(ws.readyState).toBe(3); // CLOSED
  });
});
