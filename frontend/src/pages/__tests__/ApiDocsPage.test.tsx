import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import ApiDocsPage from '../ApiDocsPage';

vi.mock('../../api/client', () => ({
  apiClient: {
    get: vi.fn().mockResolvedValue({
      status: 200,
      data: { total_connectors: 7, healthy_connectors: 7 },
    }),
    post: vi.fn().mockResolvedValue({
      status: 200,
      data: {
        transaction_id: 'txn_994821',
        risk_score: 942,
        decision: 'BLOCK_AND_ESCALATE',
      },
    }),
  },
}));

describe('ApiDocsPage Developer Portal Tests', () => {
  const renderComponent = () => {
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    return render(
      <QueryClientProvider client={queryClient}>
        <ApiDocsPage />
      </QueryClientProvider>
    );
  };

  it('renders portal header, quick links, and endpoints directory', () => {
    renderComponent();

    expect(screen.getByText(/Developer & API Reference Portal/i)).toBeInTheDocument();
    expect(screen.getByText(/Scalar Portal/i)).toBeInTheDocument();
    expect(screen.getByText(/Swagger UI/i)).toBeInTheDocument();
    expect(screen.getByText(/Consortium Endpoints/i)).toBeInTheDocument();
    expect(screen.getByText(/Score Transaction with PyTorch GAT/i)).toBeInTheDocument();
  });

  it('switches between code snippet languages (Python, Java, Node, cURL)', () => {
    renderComponent();

    const pythonTab = screen.getByRole('button', { name: 'PYTHON' });
    fireEvent.click(pythonTab);
    expect(screen.getByText(/import httpx/i)).toBeInTheDocument();

    const nodeTab = screen.getByRole('button', { name: 'NODE' });
    fireEvent.click(nodeTab);
    expect(screen.getByText(/import axios/i)).toBeInTheDocument();

    const javaTab = screen.getByRole('button', { name: 'JAVA' });
    fireEvent.click(javaTab);
    expect(screen.getByText(/OkHttpClient/i)).toBeInTheDocument();
  });

  it('executes live interactive API request runner and displays response payload', async () => {
    renderComponent();

    const executeBtn = screen.getByRole('button', { name: /Execute Request/i });
    fireEvent.click(executeBtn);

    await waitFor(() => {
      expect(screen.getByText(/Live Server Response/i)).toBeInTheDocument();
      expect(screen.getByText(/HTTP 200 OK/i)).toBeInTheDocument();
      expect(screen.getByText(/BLOCK_AND_ESCALATE/i)).toBeInTheDocument();
    });
  });
});
