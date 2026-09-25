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
 * Implements non-abandoning exponential backoff reconnection, dynamic RTT latency measurement,
 * and seamless fallback transition without permanent dead-ends.
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
  const reconnectTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pingIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const mockIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const reconnectAttemptsRef = useRef<number>(0);
  const lastPingTimestampRef = useRef<number>(0);

  useEffect(() => {
    let isMounted = true;

    function stopMockFallback() {
      if (mockIntervalRef.current) {
        clearInterval(mockIntervalRef.current);
        mockIntervalRef.current = null;
      }
    }

    function startMockFallbackStream() {
      if (mockIntervalRef.current) return;
      setStatus('mock_active');

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

    function scheduleReconnect() {
      if (!isMounted) return;
      if (reconnectTimeoutRef.current) {
        clearTimeout(reconnectTimeoutRef.current);
      }
      reconnectAttemptsRef.current += 1;

      // Exponential backoff with jitter: 1.5s, 2.25s, 3.4s, 5.0s ... capped at 15s
      const baseDelay = Math.min(1000 * Math.pow(1.5, reconnectAttemptsRef.current), 15000);
      const jitter = Math.random() * 500;
      const delay = Math.round(baseDelay + jitter);

      if (reconnectAttemptsRef.current > 2) {
        startMockFallbackStream();
      } else {
        setStatus('disconnected');
      }

      // CRITICAL: NEVER abandon reconnection! Always schedule retry even when mock_active
      reconnectTimeoutRef.current = setTimeout(() => {
        if (isMounted) {
          connect();
        }
      }, delay);
    }

    function sendPing(ws: WebSocket) {
      if (ws.readyState === 1 || (typeof WebSocket !== 'undefined' && ws.readyState === WebSocket.OPEN)) {
        lastPingTimestampRef.current = performance.now();
        try {
          ws.send(JSON.stringify({ type: 'ping', timestamp: Date.now() }));
        } catch {
          // Socket might have closed concurrently
        }
      }
    }

    function connect() {
      if (!isMounted) return;

      // Clean up previous socket if still lingering
      if (wsRef.current) {
        try {
          wsRef.current.onopen = null;
          wsRef.current.onmessage = null;
          wsRef.current.onerror = null;
          wsRef.current.onclose = null;
          if (wsRef.current.readyState === WebSocket.OPEN || wsRef.current.readyState === WebSocket.CONNECTING) {
            wsRef.current.close(1000, 'Reconnecting');
          }
        } catch {
          // Ignore
        }
        wsRef.current = null;
      }

      const url = getWebSocketUrl();
      if (!url) {
        scheduleReconnect();
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

          // Immediately terminate offline simulated stream if it was running
          stopMockFallback();

          // Real round-trip latency probe
          sendPing(ws);

          // Periodic heartbeat ping every 10 seconds to maintain real dynamic latency telemetry
          if (pingIntervalRef.current) clearInterval(pingIntervalRef.current);
          pingIntervalRef.current = setInterval(() => {
            if (isMounted && wsRef.current) {
              sendPing(wsRef.current);
            }
          }, 10000);
        };

        ws.onmessage = (event) => {
          if (!isMounted) return;
          try {
            const data = JSON.parse(event.data);
            const eventType = (data.event_type || data.event || '').toUpperCase();

            // Calculate real round-trip latency on PONG response from backend
            if (eventType === 'PONG') {
              if (lastPingTimestampRef.current > 0) {
                const elapsed = performance.now() - lastPingTimestampRef.current;
                if (elapsed > 0 && elapsed < 30000) {
                  setLatencyMs(Math.max(0.5, Math.round(elapsed * 10) / 10));
                }
              }
              return;
            }

            if (eventType === 'CONNECTED') {
              setStatus('connected');
              stopMockFallback();
              sendPing(ws);
              return;
            }

            if (data.payload && (eventType === 'ALERT_TRIGGERED' || eventType === 'TRANSACTION_SCORED')) {
              pushStreamEvent(data.payload as LiveStreamTransaction);
            }
          } catch (e) {
            console.warn('[WebSocket Parsing Error]', e);
          }
        };

        ws.onerror = () => {
          if (!isMounted) return;
          try {
            if (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING) {
              ws.close();
            }
          } catch {
            // Socket already terminating
          }
        };

        ws.onclose = () => {
          if (!isMounted) return;
          if (pingIntervalRef.current) {
            clearInterval(pingIntervalRef.current);
            pingIntervalRef.current = null;
          }
          scheduleReconnect();
        };
      } catch {
        scheduleReconnect();
      }
    }

    connect();

    return () => {
      isMounted = false;
      stopMockFallback();
      if (pingIntervalRef.current) {
        clearInterval(pingIntervalRef.current);
        pingIntervalRef.current = null;
      }
      if (reconnectTimeoutRef.current) {
        clearTimeout(reconnectTimeoutRef.current);
        reconnectTimeoutRef.current = null;
      }
      if (wsRef.current) {
        const socket = wsRef.current;
        socket.onopen = null;
        socket.onmessage = null;
        socket.onerror = null;
        socket.onclose = null;
        try {
          if (socket.readyState === 1 || (typeof WebSocket !== 'undefined' && socket.readyState === WebSocket.OPEN)) {
            socket.close(1000, 'Component unmounted');
          } else if (socket.readyState === 0 || (typeof WebSocket !== 'undefined' && socket.readyState === WebSocket.CONNECTING)) {
            socket.onopen = () => {
              try {
                socket.close(1000, 'Unmounted while connecting');
              } catch {
                // Ignore socket closure errors
              }
            };
          }
        } catch {
          // Socket already closed or terminating
        }
        wsRef.current = null;
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
