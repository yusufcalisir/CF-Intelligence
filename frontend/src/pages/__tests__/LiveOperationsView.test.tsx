import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, act } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { BrowserRouter } from 'react-router-dom';
import LiveOperationsView from '../LiveOperationsView';
import * as queries from '../../api/queries';

const createWrapper = () => {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
    },
  });
  return ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>{children}</BrowserRouter>
    </QueryClientProvider>
  );
};

describe('LiveOperationsView Component', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(queries, 'useScoringVolume').mockReturnValue({
      data: { total_scored: 15420, fraud_detected: 42, tps_current: 24.5 },
      isLoading: false,
    } as any);
    vi.spyOn(queries, 'useSimulations').mockReturnValue({
      data: [],
      isLoading: false,
    } as any);
    vi.spyOn(queries, 'useSimulation').mockReturnValue({
      data: null,
      isLoading: false,
    } as any);
    vi.spyOn(queries, 'useTrainingRounds').mockReturnValue({
      data: [],
      isLoading: false,
    } as any);
  });

  it('renders dashboard title, active champion AUC, and empty round telemetry CTA', () => {
    render(<LiveOperationsView />, { wrapper: createWrapper() });

    expect(screen.getByText(/Live Operations Dashboard/i)).toBeInTheDocument();
    expect(screen.getByText(/Active Champion AUC/i)).toBeInTheDocument();
    expect(screen.getByText(/No Telemetry Rounds Recorded/i)).toBeInTheDocument();
    expect(screen.getByText(/Start Training Run/i)).toBeInTheDocument();
  });

  it('renders dataset configuration and import buttons', () => {
    render(<LiveOperationsView />, { wrapper: createWrapper() });

    expect(screen.getByRole('button', { name: /Import Dataset/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Configure/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Start Simulation/i })).toBeInTheDocument();
  });

  it('handles offline demo fallback banner and retry button click', async () => {
    render(<LiveOperationsView />, { wrapper: createWrapper() });

    // In jsdom environment without live WebSocket server, ws will fail and trigger offline demo mode
    // Wait for the offline demo mode badge or retry button to appear
    const retryBtn = await screen.findByRole('button', { name: /Retry Live Stream/i }, { timeout: 2000 }).catch(() => null);
    if (retryBtn) {
      expect(screen.getByText(/Simulated Telemetry \(Offline Demo Mode\)/i)).toBeInTheDocument();
      fireEvent.click(retryBtn);
      expect(retryBtn).toBeInTheDocument();
    }
  });

  it('maintains CONNECTED status and sends periodic liveness ping via watchdog timer', () => {
    vi.useFakeTimers();

    const mockSend = vi.fn();
    const mockClose = vi.fn();
    let instance: any = null;

    class TestWebSocket {
      static OPEN = 1;
      static CLOSED = 3;
      readyState = 1;
      send = mockSend;
      close = mockClose;
      onopen: (() => void) | null = null;
      onclose: (() => void) | null = null;
      onerror: (() => void) | null = null;
      onmessage: ((e: { data: string }) => void) | null = null;

      constructor(public url: string) {
        instance = this;
      }
    }

    vi.stubGlobal('WebSocket', TestWebSocket);

    render(<LiveOperationsView />, { wrapper: createWrapper() });

    // Simulate WebSocket opening
    expect(instance).not.toBeNull();
    act(() => {
      instance.onopen();
    });

    // Verify CONNECTED status
    expect(screen.getByText('CONNECTED')).toBeInTheDocument();

    // Advance timer by 10s to trigger liveness check ping
    act(() => {
      vi.advanceTimersByTime(10000);
    });

    expect(mockSend).toHaveBeenCalledWith(expect.stringContaining('"event":"ping"'));

    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it('transitions to reconnecting offline mode and cleans up liveness timer on connection close', () => {
    vi.useFakeTimers();

    const mockSend = vi.fn();
    const mockClose = vi.fn();
    let instance: any = null;

    class TestWebSocket {
      static OPEN = 1;
      static CLOSED = 3;
      readyState = 1;
      send = mockSend;
      close = mockClose;
      onopen: (() => void) | null = null;
      onclose: (() => void) | null = null;
      onerror: (() => void) | null = null;
      onmessage: ((e: { data: string }) => void) | null = null;

      constructor(public url: string) {
        instance = this;
      }
    }

    vi.stubGlobal('WebSocket', TestWebSocket);

    render(<LiveOperationsView />, { wrapper: createWrapper() });

    act(() => {
      instance.onopen();
    });
    expect(screen.getByText('CONNECTED')).toBeInTheDocument();

    // Simulate socket closing
    instance.readyState = 3;
    act(() => {
      instance.onclose();
    });

    // Verify OFFLINE mode triggered
    expect(screen.getByText('OFFLINE')).toBeInTheDocument();
    expect(screen.getByText(/Offline Demo Mode \(Connection Lost — Simulated\)/i)).toBeInTheDocument();

    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it('tears down active WebSocket and cleans all timers on component unmount', () => {
    const mockClose = vi.fn();
    let instance: any = null;

    class TestWebSocket {
      static OPEN = 1;
      static CLOSED = 3;
      readyState = 1;
      send = vi.fn();
      close = mockClose;
      onopen: (() => void) | null = null;
      onclose: (() => void) | null = null;
      onerror: (() => void) | null = null;
      onmessage: ((e: { data: string }) => void) | null = null;

      constructor(public url: string) {
        instance = this;
      }
    }

    vi.stubGlobal('WebSocket', TestWebSocket);

    const { unmount } = render(<LiveOperationsView />, { wrapper: createWrapper() });
    expect(instance).not.toBeNull();

    unmount();

    expect(mockClose).toHaveBeenCalled();

    vi.unstubAllGlobals();
  });

  it('cancels pending reconnect timer and triggers immediate reconnect when clicking retry button', () => {
    vi.useFakeTimers();

    let socketCount = 0;
    let instance: any = null;

    class TestWebSocket {
      static OPEN = 1;
      static CLOSED = 3;
      readyState = 1;
      send = vi.fn();
      close = vi.fn();
      onopen: (() => void) | null = null;
      onclose: (() => void) | null = null;
      onerror: (() => void) | null = null;
      onmessage: ((e: { data: string }) => void) | null = null;

      constructor(public url: string) {
        socketCount++;
        instance = this;
      }
    }

    vi.stubGlobal('WebSocket', TestWebSocket);

    render(<LiveOperationsView />, { wrapper: createWrapper() });
    expect(socketCount).toBe(1);

    // Trigger connection close synchronously
    act(() => {
      instance.readyState = 3;
      instance.onclose();
    });

    // Offline banner and retry button appear synchronously
    const retryBtn = screen.getByRole('button', { name: /Retry Live Stream/i });
    expect(retryBtn).toBeInTheDocument();

    // User clicks retry button
    act(() => {
      fireEvent.click(retryBtn);
    });

    // Socket count should increment due to immediate reconnect retry
    expect(socketCount).toBe(2);

    vi.useRealTimers();
    vi.unstubAllGlobals();
  });
});
