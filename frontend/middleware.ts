/**
 * Vercel Edge Middleware for Layer-2 Distributed Rate Limiting & Gateway Protection.
 *
 * Runs in V8 Isolates globally across 300+ Edge locations (<5ms execution latency)
 * to throttle abusive traffic before it reaches downstream backend services.
 */

import { Ratelimit } from '@upstash/ratelimit';
import { Redis } from '@upstash/redis';

// Detect Upstash Redis or Vercel KV credentials
const redisUrl =
  process.env.UPSTASH_REDIS_REST_URL ||
  process.env.KV_REST_API_URL ||
  '';

const redisToken =
  process.env.UPSTASH_REDIS_REST_TOKEN ||
  process.env.KV_REST_API_TOKEN ||
  '';

let redis: Redis | null = null;
let apiLimiter: Ratelimit | null = null;
let predictLimiter: Ratelimit | null = null;

if (redisUrl && redisToken) {
  try {
    redis = new Redis({
      url: redisUrl,
      token: redisToken,
    });

    // 1. General API Sliding Window Limiter: 60 reqs / 1 min
    apiLimiter = new Ratelimit({
      redis,
      limiter: Ratelimit.slidingWindow(60, '1 m'),
      analytics: true,
      prefix: '@cfi/edge/api',
    });

    // 2. Heavy ML Inference Sliding Window Limiter: 20 reqs / 1 min
    predictLimiter = new Ratelimit({
      redis,
      limiter: Ratelimit.slidingWindow(20, '1 m'),
      analytics: true,
      prefix: '@cfi/edge/predict',
    });
  } catch (err) {
    console.warn('Failed to initialize Edge Ratelimit Redis client:', err);
  }
}

export interface EdgeRateLimitResult {
  success: boolean;
  limit: number;
  remaining: number;
  reset: number;
}

/**
 * Evaluates rate limit for a client IP and target URL path.
 * Exported for modularity and unit testability.
 */
export async function evaluateEdgeRateLimit(
  clientIp: string,
  pathname: string
): Promise<EdgeRateLimitResult | null> {
  if (!apiLimiter || !predictLimiter) {
    // Fail-open if Redis is not configured (graceful degradation)
    return null;
  }

  try {
    if (pathname.startsWith('/api/v1/predict') || pathname.startsWith('/api/v1/score-transaction')) {
      return await predictLimiter.limit(clientIp);
    }
    if (pathname.startsWith('/api/')) {
      return await apiLimiter.limit(clientIp);
    }
    return null;
  } catch (err) {
    console.warn('Edge rate limit check failed (failing open):', err);
    return null;
  }
}

/**
 * Standard Vercel Edge Middleware handler.
 */
export default async function middleware(request: Request): Promise<Response | undefined> {
  const url = new URL(request.url);
  const { pathname } = url;

  // Bypass static assets, fonts, icons, and Vite bundle artifacts
  if (
    pathname.startsWith('/assets') ||
    pathname.startsWith('/static') ||
    pathname.endsWith('.js') ||
    pathname.endsWith('.css') ||
    pathname.endsWith('.png') ||
    pathname.endsWith('.svg') ||
    pathname.endsWith('.ico') ||
    pathname.endsWith('.woff2')
  ) {
    return undefined;
  }

  // Resolve Real Client IP (Priority: Cloudflare > Vercel > X-Forwarded-For > Fallback)
  const clientIp =
    request.headers.get('cf-connecting-ip') ||
    request.headers.get('x-real-ip') ||
    request.headers.get('x-forwarded-for')?.split(',')[0]?.trim() ||
    '127.0.0.1';

  const result = await evaluateEdgeRateLimit(clientIp, pathname);

  if (result && !result.success) {
    const retryAfter = Math.max(1, Math.ceil((result.reset - Date.now()) / 1000));
    return new Response(
      JSON.stringify({
        type: 'https://cfi-platform.org/errors/EdgeRateLimitExceeded',
        title: 'Too Many Requests (Vercel Edge Gateway)',
        status: 429,
        detail: `Rate limit of ${result.limit} requests/min exceeded for client. Throttled at Edge network.`,
        instance: pathname,
        retryAfter,
      }),
      {
        status: 429,
        headers: {
          'Content-Type': 'application/problem+json',
          'Retry-After': String(retryAfter),
          'X-RateLimit-Limit': String(result.limit),
          'X-RateLimit-Remaining': '0',
          'X-RateLimit-Reset': String(result.reset),
          'X-Edge-Throttled': 'true',
        },
      }
    );
  }

  return undefined;
}
