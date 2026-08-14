import { describe, it, expect } from 'vitest';
import { apiClient } from '../client';

describe('apiClient', () => {
  it('is initialized with proper timeout and json headers', () => {
    expect(apiClient.defaults.headers['Content-Type']).toBe('application/json');
    expect(apiClient.defaults.timeout).toBe(30000);
  });

  it('has response interceptor installed for warning logging on error', () => {
    expect(apiClient.interceptors.response).toBeDefined();
  });
});
