import React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { BrowserRouter } from 'react-router-dom';
import PrivacyDefensePage from '../PrivacyDefensePage';

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

describe('PrivacyDefensePage', () => {
  it('renders privacy defense header and attack audit modules', () => {
    render(<PrivacyDefensePage />, { wrapper: createWrapper() });

    expect(screen.getByText(/Privacy Defense & Byzantine Suite/i)).toBeInTheDocument();
    expect(screen.getByText(/Zero Raw PII Verified/i)).toBeInTheDocument();
    expect(screen.getByText(/Byzantine Defenses/i)).toBeInTheDocument();
    expect(screen.getByText(/Attack Audits/i)).toBeInTheDocument();
    expect(screen.getByText(/Privacy Budget Log/i)).toBeInTheDocument();
  });
});
