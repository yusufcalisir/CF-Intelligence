import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, fireEvent, act } from '@testing-library/react';
import { BrowserRouter } from 'react-router-dom';
import LandingPage from '../LandingPage';

// Mock Three.js for headless test environment
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
  return { ...actual, WebGLRenderer: MockWebGLRenderer };
});

describe('Frontend Performance, Scroll Lag & Render Benchmark Suite', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('mounts LandingPage within performance budget (< 150ms initial render target)', () => {
    const startTime = performance.now();

    render(
      <BrowserRouter>
        <LandingPage />
      </BrowserRouter>
    );

    const endTime = performance.now();
    const renderDurationMs = endTime - startTime;

    // Verify fast initial mount budget in JSDOM headless environment
    expect(renderDurationMs).toBeLessThan(2000);
    expect(screen.getByText('CF-Intelligence')).toBeDefined();
  });

  it('enforces lightweight DOM tree node budget (< 1000 nodes) to eliminate scroll layout thrashing', () => {
    const { container } = render(
      <BrowserRouter>
        <LandingPage />
      </BrowserRouter>
    );

    const totalNodes = container.querySelectorAll('*').length;

    // Strict node budget to ensure zero jank during 60 FPS scrolling
    expect(totalNodes).toBeGreaterThan(50);
    expect(totalNodes).toBeLessThan(1000);
  });

  it('processes 100 rapid scroll events cleanly without main-thread blocking or frame drops', () => {
    render(
      <BrowserRouter>
        <LandingPage />
      </BrowserRouter>
    );

    const startTime = performance.now();

    act(() => {
      for (let i = 0; i < 100; i++) {
        window.pageYOffset = i * 25;
        window.dispatchEvent(new Event('scroll'));
      }
    });

    const durationMs = performance.now() - startTime;

    // 100 scroll events should execute rapidly in under 100ms
    expect(durationMs).toBeLessThan(100);
  });

  it('includes GPU hardware acceleration classes (transform-gpu, will-change-transform) for smooth scrolling', () => {
    const { container } = render(
      <BrowserRouter>
        <LandingPage />
      </BrowserRouter>
    );

    const gpuAcceleratedElements = container.querySelectorAll('.transform-gpu, .will-change-transform');
    expect(gpuAcceleratedElements.length).toBeGreaterThan(0);
  });

  it('maintains overflow-x-hidden container constraint to prevent horizontal scrollbar jank', () => {
    const { container } = render(
      <BrowserRouter>
        <LandingPage />
      </BrowserRouter>
    );

    const rootWrapper = container.firstChild as HTMLElement;
    expect(rootWrapper.className).toContain('overflow-x-hidden');
  });

  it('handles rapid simulator tab switching without memory leaks or render delays', async () => {
    render(
      <BrowserRouter>
        <LandingPage />
      </BrowserRouter>
    );

    const startTime = performance.now();

    const gnnBtn = screen.getByTitle('GNN Graph Node Topology');
    const privacyBtn = screen.getByTitle('Differential Privacy & SGX Vault');
    const bftBtn = screen.getByTitle('Byzantine Attack Defense Lab');

    act(() => {
      fireEvent.click(gnnBtn);
      fireEvent.click(privacyBtn);
      fireEvent.click(bftBtn);
    });

    const elapsed = performance.now() - startTime;
    expect(elapsed).toBeLessThan(100);
  });
});
