import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { BrowserRouter } from 'react-router-dom';
import LandingPage from '../LandingPage';

// Hoisted Mock for Three.js WebGLRenderer in headless Vitest runner
vi.mock('three', async (importOriginal) => {
  const actual = await importOriginal<typeof import('three')>();

  class MockWebGLRenderer {
    domElement = document.createElement('canvas');
    shadowMap = { enabled: false, type: 0 };

    setSize() {}
    setPixelRatio() {}
    render() {}
    dispose() {}
    clear() {}
  }

  return {
    ...actual,
    WebGLRenderer: MockWebGLRenderer,
  };
});

describe('LandingPage Architecture & Navigation Component Tests', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('renders CF-Intelligence brand header with logo', () => {
    render(
      <BrowserRouter>
        <LandingPage />
      </BrowserRouter>
    );

    expect(screen.getAllByText('CF-Intelligence').length).toBeGreaterThan(0);
  });

  it('contains all 8 required section anchor targets for complete component access', () => {
    const { container } = render(
      <BrowserRouter>
        <LandingPage />
      </BrowserRouter>
    );

    const requiredSectionIds = [
      'hero',
      'problem-solution',
      'how-it-works',
      'product',
      'platform',
      'architecture',
      'security',
      'api',
      'docs',
      'contact',
    ];

    requiredSectionIds.forEach((id) => {
      const section = container.querySelector(`#${id}`);
      expect(section).not.toBeNull();
    });
  });

  it('renders top floating navigation links covering all major components', () => {
    render(
      <BrowserRouter>
        <LandingPage />
      </BrowserRouter>
    );

    expect(screen.getByText('Overview')).toBeDefined();
    expect(screen.getByText('Platform & Engine')).toBeDefined();
    expect(screen.getByText('Architecture & Security')).toBeDefined();
  });

  it('renders call to action buttons leading to live platform demo dashboard', () => {
    render(
      <BrowserRouter>
        <LandingPage />
      </BrowserRouter>
    );

    const launchDemoButtons = screen.getAllByText(/Launch Demo|Launch Live Platform Demo/i);
    expect(launchDemoButtons.length).toBeGreaterThan(0);
  });

  it('switches views dynamically when clicking interactive dashboard preview sidebar buttons', async () => {
    render(
      <BrowserRouter>
        <LandingPage />
      </BrowserRouter>
    );

    // Default view includes telemetry & consortium topology
    expect(screen.getByText('Consortium Topology')).toBeDefined();

    // Click GNN Graph button (⬡)
    const gnnBtn = screen.getByTitle('GNN Graph Node Topology');
    fireEvent.click(gnnBtn);
    await waitFor(() => {
      expect(screen.getByText('GNN Subgraph Inspection')).toBeInTheDocument();
    });

    // Click Differential Privacy button (◎)
    const privacyBtn = screen.getByTitle('Differential Privacy & SGX Vault');
    fireEvent.click(privacyBtn);
    await waitFor(() => {
      expect(screen.getByText('Intel SGX & (ε, δ)-DP Engine')).toBeInTheDocument();
    });

    // Click Byzantine Defense button (△)
    const bftBtn = screen.getByTitle('Byzantine Attack Defense Lab');
    fireEvent.click(bftBtn);
    await waitFor(() => {
      expect(screen.getByText('Byzantine Resilience Monitor')).toBeInTheDocument();
    });

    // Click FinCEN SAR button (▦)
    const sarBtn = screen.getByTitle('FinCEN SAR XML Generator');
    fireEvent.click(sarBtn);
    await waitFor(() => {
      expect(screen.getByText('FinCEN SAR Automated Compliance')).toBeInTheDocument();
    });
  }, 15000);

  it('opens bank node detail drawer when clicking a consortium institution card', () => {
    render(
      <BrowserRouter>
        <LandingPage />
      </BrowserRouter>
    );

    const jpmCard = screen.getByText('JPMorgan Chase & Co.');
    fireEvent.click(jpmCard);

    expect(screen.getByText('Institution Node Detail')).toBeDefined();
    expect(screen.getByText('New York Data Center, US (Node #01)')).toBeDefined();
  });
});
