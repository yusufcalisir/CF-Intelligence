import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { BrowserRouter } from 'react-router-dom';
import PsiPage from '../PsiPage';

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

describe('PsiPage', () => {
  it('renders PSI page title and cryptographic controls', () => {
    render(<PsiPage />, { wrapper: createWrapper() });

    expect(screen.getByText(/Private Set Intersection \(PSI\)/i)).toBeInTheDocument();
    expect(screen.getByText(/Reconcile customer & entity identities across banks without exposing PII/i)).toBeInTheDocument();
    expect(screen.getByText(/PSI Protocol Control Center/i)).toBeInTheDocument();
  });

  it('renders fuzzy MinHash playground for testing string transliteration', () => {
    render(<PsiPage />, { wrapper: createWrapper() });

    expect(screen.getByText(/MinHash Spelling Playground/i)).toBeInTheDocument();
    expect(screen.getByText(/Name Input 1/i)).toBeInTheDocument();
    expect(screen.getByText(/Name Input 2/i)).toBeInTheDocument();
  });
});
