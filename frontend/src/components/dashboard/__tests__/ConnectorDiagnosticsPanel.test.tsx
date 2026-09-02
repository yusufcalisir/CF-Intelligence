import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import ConnectorDiagnosticsPanel from '../ConnectorDiagnosticsPanel';

// Mock queries
vi.mock('../../../api/queries', () => ({
  useConnectorDiagnostics: vi.fn(() => ({
    data: {
      total_connectors: 7,
      healthy_connectors: 7,
      avg_latency_ms: 2.4,
      connectors: [
        {
          connector_id: 'kafka',
          name: 'Apache Kafka Event Broker',
          category: 'Event Ingestion',
          status: 'HEALTHY',
          latency_ms: 3.4,
          endpoint: 'kafka.internal.consortium.net:9092',
          protocol: 'SASL_SSL / TLS 1.3',
          version: '3.7.0',
          last_checked: '2026-09-02T12:00:00Z',
          details: { topics: ['fraud.transactions.raw'] },
        },
        {
          connector_id: 'vault',
          name: 'HashiCorp Vault PKI Engine',
          category: 'Secrets & PKI',
          status: 'HEALTHY',
          latency_ms: 1.8,
          endpoint: 'https://vault.internal.consortium.net:8200',
          protocol: 'HTTPS / Mutual TLS',
          version: '1.16.2',
          last_checked: '2026-09-02T12:00:00Z',
          details: { sealed: false },
        },
      ],
    },
    isLoading: false,
    refetch: vi.fn(),
  })),
  useTestConnector: vi.fn(() => ({
    mutateAsync: vi.fn().mockResolvedValue({
      connector_id: 'kafka',
      name: 'Apache Kafka Event Broker',
      success: true,
      round_trip_ms: 3.4,
      status_code: 200,
      handshake_summary: 'SASL_SSL Handshake completed.',
      diagnostics_log: ['Initiating TLS 1.3 socket', 'SASL SCRAM-SHA-512 accepted'],
      payload_sample: { cluster_id: 'kafka-test-01' },
    }),
    isPending: false,
  })),
}));

describe('ConnectorDiagnosticsPanel Component Tests', () => {
  const renderComponent = () => {
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    return render(
      <QueryClientProvider client={queryClient}>
        <ConnectorDiagnosticsPanel />
      </QueryClientProvider>
    );
  };

  it('renders header banner, KPI statistics, and connector cards', () => {
    renderComponent();

    expect(screen.getByText(/Enterprise Connectors & Infrastructure Health/i)).toBeInTheDocument();
    expect(screen.getByText('Apache Kafka Event Broker')).toBeInTheDocument();
    expect(screen.getByText('HashiCorp Vault PKI Engine')).toBeInTheDocument();
    expect(screen.getByText(/Avg Probe Latency/i)).toBeInTheDocument();
  });

  it('triggers interactive connection ping probe on click and displays modal', async () => {
    renderComponent();

    const testButtons = screen.getAllByRole('button', { name: /Test Connection Ping/i });
    expect(testButtons.length).toBeGreaterThan(0);

    const firstButton = testButtons[0] as HTMLElement;
    fireEvent.click(firstButton);

    await waitFor(() => {

      expect(screen.getByText(/Handshake Execution Trace/i)).toBeInTheDocument();
      expect(screen.getByText(/SASL_SSL Handshake completed/i)).toBeInTheDocument();
    });

    const doneButton = screen.getByRole('button', { name: /Done/i });
    fireEvent.click(doneButton);

    await waitFor(() => {
      expect(screen.queryByText(/Handshake Execution Trace/i)).not.toBeInTheDocument();
    });
  });
});
