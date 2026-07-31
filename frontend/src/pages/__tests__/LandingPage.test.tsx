import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
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

  it('renders CF-Intelligence brand header with logo and version badge', () => {
    render(
      <BrowserRouter>
        <LandingPage />
      </BrowserRouter>
    );

    expect(screen.getByText('CF-Intelligence')).toBeDefined();
    expect(screen.getByText('v2.4.0')).toBeDefined();
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
    expect(screen.getByText('Problem')).toBeDefined();
    expect(screen.getByText('Workflow')).toBeDefined();
    expect(screen.getByText('Capabilities')).toBeDefined();
    expect(screen.getByText('Platform')).toBeDefined();
    expect(screen.getByText('Architecture')).toBeDefined();
    expect(screen.getByText('Security')).toBeDefined();
    expect(screen.getByText('API & Docs')).toBeDefined();
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

  it('switches views dynamically when clicking interactive dashboard preview sidebar buttons (C, ⬡, ◎, △, ▦)', async () => {
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
    expect(await screen.findByText('GNN Subgraph Inspection')).toBeDefined();

    // Click Differential Privacy button (◎)
    const privacyBtn = screen.getByTitle('Differential Privacy & SGX Vault');
    fireEvent.click(privacyBtn);
    expect(await screen.findByText('Intel SGX & (ε, δ)-DP Engine')).toBeDefined();

    // Click Byzantine Defense button (△)
    const bftBtn = screen.getByTitle('Byzantine Attack Defense Lab');
    fireEvent.click(bftBtn);
    expect(await screen.findByText('Byzantine Resilience Monitor')).toBeDefined();

    // Click FinCEN SAR button (▦)
    const sarBtn = screen.getByTitle('FinCEN SAR XML Generator');
    fireEvent.click(sarBtn);
    expect(await screen.findByText('FinCEN SAR Automated Compliance')).toBeDefined();
  });

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
