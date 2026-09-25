import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import PsiPage from '../PsiPage';
import { apiClient } from '../../api/client';

const renderPsiPage = (initialRoute = '/psi') => {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[initialRoute]}>
        <PsiPage />
      </MemoryRouter>
    </QueryClientProvider>
  );
};

describe('PsiPage Deep Linking & Entity Preloading', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('renders PSI page title and cryptographic controls', () => {
    renderPsiPage();

    expect(screen.getByText(/Private Set Intersection \(PSI\)/i)).toBeInTheDocument();
    expect(
      screen.getByText(/Reconcile customer & entity identities across banks without exposing PII/i)
    ).toBeInTheDocument();
    expect(screen.getByText(/PSI Protocol Control Center/i)).toBeInTheDocument();
  });

  it('renders fuzzy MinHash playground for testing string transliteration', () => {
    renderPsiPage();

    expect(screen.getByText(/MinHash Spelling Playground/i)).toBeInTheDocument();
    expect(screen.getByText(/Name Input 1/i)).toBeInTheDocument();
    expect(screen.getByText(/Name Input 2/i)).toBeInTheDocument();
  });

  it('reads URL parameters (?entity_id=...&auto_match=true), prefills inputs, executes fuzzy match, and displays deep link banner', async () => {
    const postSpy = vi.spyOn(apiClient, 'post').mockImplementation(async (url: string, payload: any) => {
      if (url.includes('/fuzzy-resolve')) {
        return {
          data: {
            matches: [
              {
                entity: {
                  id: payload.raw_identifier,
                  display_label: 'Target Suspect (Cross-Bank Nexus)',
                  entity_type: payload.entity_type,
                  privacy_id: 'priv_hmac_7712a',
                  bank_id: 'bank_b',
                  risk_level: 'critical',
                  attributes: { raw_standardized: 'TARGET SUSPECT' },
                },
                similarity_score: 0.95,
              },
            ],
          },
        };
      }
      return { data: {} };
    });

    renderPsiPage('/psi?entity_id=ent_acc_44921&auto_match=true&type=customer');

    // Verify deep link context banner rendered
    expect(screen.getByText(/Deep-Linked Entity Investigation Active/i)).toBeInTheDocument();
    expect(screen.getByText('ent_acc_44921')).toBeInTheDocument();
    expect(screen.getByText(/Auto-Matched/i)).toBeInTheDocument();

    // Verify inputs pre-filled
    const searchInput = screen.getByPlaceholderText(/e\.g\. Yusuf Çalışır/i) as HTMLInputElement;
    expect(searchInput.value).toBe('ent_acc_44921');

    // Verify mutation was called with the deep-linked entity
    await waitFor(() => {
      expect(postSpy).toHaveBeenCalledWith(
        expect.stringContaining('/fuzzy-resolve'),
        expect.objectContaining({
          raw_identifier: 'ent_acc_44921',
          entity_type: 'customer',
        })
      );
    });

    // Verify resolved cross-bank entity appears in table
    await waitFor(() => {
      expect(screen.getByText('Target Suspect (Cross-Bank Nexus)')).toBeInTheDocument();
      expect(screen.getByText(/95%/)).toBeInTheDocument();
      expect(screen.getByText(/priv_hmac_7712a/)).toBeInTheDocument();
    });
  });

  it('reads card_hash parameter (?card_hash=...&auto_match=true), infers card type, and auto-matches', async () => {
    const postSpy = vi.spyOn(apiClient, 'post').mockImplementation(async (url: string, payload: any) => {
      if (url.includes('/fuzzy-resolve')) {
        return {
          data: {
            matches: [
              {
                entity: {
                  id: payload.raw_identifier,
                  display_label: 'Compromised Card BIN 4111',
                  entity_type: 'card',
                  privacy_id: 'priv_card_pan_9981',
                  bank_id: 'bank_c',
                  risk_level: 'high',
                  attributes: { raw_standardized: 'CARD 4111' },
                },
                similarity_score: 1.0,
              },
            ],
          },
        };
      }
      return { data: {} };
    });

    renderPsiPage('/psi?card_hash=card_hash_e901a88b&auto_match=true');

    expect(screen.getByText(/Deep-Linked Entity Investigation Active/i)).toBeInTheDocument();
    expect(screen.getByText('card_hash_e901a88b')).toBeInTheDocument();

    // Verify inferred card entity type
    const searchSelect = screen.getByDisplayValue(/Card Hash \/ PAN/i);
    expect(searchSelect).toBeInTheDocument();

    await waitFor(() => {
      expect(postSpy).toHaveBeenCalledWith(
        expect.stringContaining('/fuzzy-resolve'),
        expect.objectContaining({
          raw_identifier: 'card_hash_e901a88b',
          entity_type: 'card',
        })
      );
    });
  });

  it('allows clearing deep-link filter and restores default playground state', async () => {
    renderPsiPage('/psi?entity_id=ent_acc_temporary&auto_match=false');

    expect(screen.getByText(/Deep-Linked Entity Investigation Active/i)).toBeInTheDocument();
    expect(screen.getByText('ent_acc_temporary')).toBeInTheDocument();

    const clearBtn = screen.getByRole('button', { name: /Clear Deep Link/i });
    fireEvent.click(clearBtn);

    // Deep link banner should disappear
    expect(screen.queryByText(/Deep-Linked Entity Investigation Active/i)).not.toBeInTheDocument();

    // Search query resets to default
    const searchInput = screen.getByPlaceholderText(/e\.g\. Yusuf Çalışır/i) as HTMLInputElement;
    expect(searchInput.value).toBe('Yusuf Calisir');
  });
});
