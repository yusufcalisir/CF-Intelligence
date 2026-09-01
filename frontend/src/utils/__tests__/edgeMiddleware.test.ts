import { describe, it, expect, vi, beforeEach } from 'vitest';
import middleware, { evaluateEdgeRateLimit } from '../../../middleware';

describe('Vercel Edge Middleware (Layer 2 Gateway Protection)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('bypasses static assets and bundle files without throttling', async () => {
    const staticPaths = [
      'https://cf-intelligence.vercel.app/assets/index-CSgc4sCg.css',
      'https://cf-intelligence.vercel.app/assets/index-Dx8GqRfM.js',
      'https://cf-intelligence.vercel.app/favicon.ico',
      'https://cf-intelligence.vercel.app/logo.png',
      'https://cf-intelligence.vercel.app/static/font.woff2',
    ];

    for (const url of staticPaths) {
      const request = new Request(url, { method: 'GET' });
      const response = await middleware(request);
      expect(response).toBeUndefined(); // undefined means pass-through to CDN
    }
  });

  it('gracefully fails open when Redis is unconfigured or unavailable', async () => {
    const result = await evaluateEdgeRateLimit('192.168.1.5', '/api/v1/alerts');
    expect(result).toBeNull();

    const request = new Request('https://cf-intelligence.vercel.app/api/v1/alerts', {
      headers: { 'cf-connecting-ip': '203.0.113.50' },
    });
    const response = await middleware(request);
    expect(response).toBeUndefined(); // Passes through to backend
  });
});
