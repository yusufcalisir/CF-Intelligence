import { useEffect, useRef } from 'react';
import { useLiveAlertStore } from '../stores/useLiveAlertStore';
import type { LiveStreamTransaction } from '../stores/useLiveAlertStore';

function getWebSocketUrl(): string {
  if (typeof window === 'undefined') return '';
  if (import.meta.env.VITE_WS_URL) {
    return import.meta.env.VITE_WS_URL.replace(/\/+$/, '') + '/ws/telemetry';
  }
  if (import.meta.env.VITE_API_URL) {
    const apiUrl = import.meta.env.VITE_API_URL;
    const wsProto = apiUrl.startsWith('https') ? 'wss:' : 'ws:';
    const host = apiUrl.replace(/^https?:\/\//, '').replace(/\/+$/, '');
    return `${wsProto}//${host}/ws/telemetry`;
  }
  // Local development fallback
  if (window.location.port === '5173' || window.location.port === '3000' || window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1') {
    const isHttps = window.location.protocol === 'https:';
    const protocol = isHttps ? 'wss:' : 'ws:';
    return `${protocol}//127.0.0.1:8000/ws/telemetry`;
  }
  if (window.location.hostname.includes('hf.space')) {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    return `${protocol}//${window.location.host}/ws/telemetry`;
  }
  // Production fallback to live Hugging Face Spaces backend
  return 'wss://yusufcalisir-collaborative-fraud-intelligence-simulator.hf.space/ws/telemetry';
}


/**
 * Global Real-Time WebSocket Hook for Platform Telemetry and Fraud Alert Streaming.
 * Handles automatic reconnect with exponential backoff and seamless mock fallback.
 */
export function useRealTimeFraudStream() {
  const {
    status,
    latencyMs,
    totalStreamedTransactions,
    recentTransactions,
    activeAlertToasts,
    setStatus,
    setLatencyMs,
    pushStreamEvent,
    dismissToast,
    clearAllToasts,
  } = useLiveAlertStore();

  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimeoutRef = useRef<NodeJS.Timeout | null>(null);
  const mockIntervalRef = useRef<NodeJS.Timeout | null>(null);
  const reconnectAttemptsRef = useRef<number>(0);

  useEffect(() => {
    let isMounted = true;

    function startMockFallbackStream() {
      if (mockIntervalRef.current) clearInterval(mockIntervalRef.current);
      setStatus('mock_active');
      setLatencyMs(1.8);

      const banks = ['bank_alpha', 'bank_beta', 'bank_gamma'];
      const typologies = [
        { typ: 'RAPID_CROSS_BANK_LAYERING', desc: 'High-Velocity Cross-Bank Transfer Burst', sev: 'critical' as const, score: 942 },
        { typ: 'STRUCTURED_SMURFING', desc: 'Sub-Threshold Structured Smurfing Deposit', sev: 'high' as const, score: 815 },
        { typ: 'GNN_TOPOLOGICAL_ANOMALY', desc: 'GraphSAGE 2-Hop Layering Syndicate', sev: 'critical' as const, score: 895 },
        { typ: 'NEW_ACCOUNT_HIGH_VALUE_CRYPTO', desc: 'New Account High-Value Crypto Transfer', sev: 'high' as const, score: 780 },
        { typ: 'LEGITIMATE_PAYMENT', desc: 'Standard Retail Interbank Transfer', sev: 'info' as const, score: 120 },
      ];

      mockIntervalRef.current = setInterval(() => {
        if (!isMounted) return;
        const item = typologies[Math.floor(Math.random() * typologies.length)] ?? typologies[0]!;
        const bank = banks[Math.floor(Math.random() * banks.length)] ?? 'bank_alpha';
        const txn: LiveStreamTransaction = {
          transaction_id: `txn_${Math.floor(Date.now() % 1000000).toString().padStart(6, '0')}`,
          bank_id: bank,
          risk_score: item.score,
          severity: item.sev,
          typology: item.typ,
          description: item.desc,
          amount: Math.round((Math.random() * 250000 + 1500) * 100) / 100,
          currency: 'EUR',
          created_at: new Date().toISOString(),
        };
        pushStreamEvent(txn);
      }, 5000);

    }

    function connect() {
      if (!isMounted) return;
      const url = getWebSocketUrl();
      if (!url) {
        startMockFallbackStream();
        return;
      }

      setStatus('connecting');

      try {
        const ws = new WebSocket(url);
        wsRef.current = ws;

        ws.onopen = () => {
          if (!isMounted) return;
          reconnectAttemptsRef.current = 0;
          setStatus('connected');
          setLatencyMs(2.4);
          if (mockIntervalRef.current) {
            clearInterval(mockIntervalRef.current);
            mockIntervalRef.current = null;
          }
        };

        ws.onmessage = (event) => {
          if (!isMounted) return;
          try {
            const data = JSON.parse(event.data);
            if (data.payload && (data.event_type === 'ALERT_TRIGGERED' || data.event_type === 'TRANSACTION_SCORED')) {
              pushStreamEvent(data.payload as LiveStreamTransaction);
            }
          } catch (e) {
            console.warn('[WebSocket Parsing Error]', e);
          }
        };

        ws.onerror = () => {
          if (!isMounted) return;
          ws.close();
        };

        ws.onclose = () => {
          if (!isMounted) return;
          setStatus('disconnected');
          reconnectAttemptsRef.current += 1;

          if (reconnectAttemptsRef.current > 2) {
            // Switch to graceful simulated mock stream
            startMockFallbackStream();
          } else {
            const delay = Math.min(1000 * Math.pow(2, reconnectAttemptsRef.current), 8000);
            reconnectTimeoutRef.current = setTimeout(connect, delay);
          }
        };
      } catch {
        startMockFallbackStream();
      }
    }

    connect();

    return () => {
      isMounted = false;
      if (wsRef.current) {
        wsRef.current.close();
      }
      if (reconnectTimeoutRef.current) {
        clearTimeout(reconnectTimeoutRef.current);
      }
      if (mockIntervalRef.current) {
        clearInterval(mockIntervalRef.current);
      }
    };
  }, [pushStreamEvent, setLatencyMs, setStatus]);

  return {
    status,
    latencyMs,
    totalStreamedTransactions,
    recentTransactions,
    activeAlertToasts,
    dismissToast,
    clearAllToasts,
  };
}
