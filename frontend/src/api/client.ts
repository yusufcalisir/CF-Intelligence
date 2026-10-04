import axios from 'axios';

const API_BASE = import.meta.env.VITE_API_URL ?? '';

export const apiClient = axios.create({
  baseURL: API_BASE,
  headers: { 'Content-Type': 'application/json' },
  timeout: 30000,
});

apiClient.interceptors.request.use((config) => {
  if (typeof window !== 'undefined') {
    const token = localStorage.getItem('cfi_token') || sessionStorage.getItem('cfi_token');
    if (token && !config.headers.Authorization) {
      config.headers.Authorization = `Bearer ${token}`;
    }
    const tenantId = localStorage.getItem('cfi_tenant_id') || sessionStorage.getItem('cfi_tenant_id');
    if (tenantId && !config.headers['X-Tenant-ID']) {
      config.headers['X-Tenant-ID'] = tenantId;
    }
  }
  return config;
});

apiClient.interceptors.response.use(
  (response) => response,
  (error) => {
    if (axios.isCancel(error)) {
      // Normal React / TanStack Query query cancellation on unmount or re-render
      return Promise.reject(error);
    }
    const isTest =
      (typeof process !== 'undefined' && process.env?.NODE_ENV === 'test') ||
      (typeof import.meta !== 'undefined' && import.meta.env?.MODE === 'test');
    if (!isTest) {
      if (error.code === 'ECONNABORTED' || error.message?.includes('timeout')) {
        console.warn('[API Warning] Request timed out, using cached/fallback state:', error.config?.url);
      } else {
        console.warn('[API Warning]', error.response?.data ?? error.message);
      }
    }
    return Promise.reject(error);
  },
);

export function getActiveTenantId(): string {
  if (typeof window !== 'undefined') {
    return localStorage.getItem('cfi_tenant_id') || sessionStorage.getItem('cfi_tenant_id') || 'default';
  }
  return 'default';
}

export function setClientTenant(tenantId: string | null): void {
  if (typeof window !== 'undefined') {
    if (tenantId) {
      localStorage.setItem('cfi_tenant_id', tenantId);
    } else {
      localStorage.removeItem('cfi_tenant_id');
      sessionStorage.removeItem('cfi_tenant_id');
    }
  }
}

export function switchActiveTenant(queryClient: { clear: () => void }, newTenantId: string): void {
  setClientTenant(newTenantId);
  queryClient.clear();
}
