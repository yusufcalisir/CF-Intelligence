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

/**
 * Safely extract and format an API error message into a displayable string.
 * Prevents React Minified Error #31 caused by rendering raw error objects or
 * FastAPI/Pydantic validation error detail arrays: [{ loc, msg, type }].
 */
export function formatApiError(err: unknown, defaultMessage = 'An unexpected error occurred'): string {
  if (!err) return defaultMessage;
  const anyErr = err as any;
  const detail = anyErr?.response?.data?.detail ?? anyErr?.data?.detail;

  if (typeof detail === 'string') {
    return detail.trim() || defaultMessage;
  }

  if (Array.isArray(detail)) {
    const formatted = detail
      .map((item) => {
        if (typeof item === 'string') return item;
        if (item && typeof item === 'object') {
          const locStr = Array.isArray(item.loc) ? item.loc.join('.') : '';
          const msg = item.msg || item.message || JSON.stringify(item);
          return locStr ? `${locStr}: ${msg}` : msg;
        }
        return String(item);
      })
      .filter(Boolean)
      .join('; ');
    return formatted || defaultMessage;
  }

  if (detail && typeof detail === 'object') {
    return (detail as any).msg || (detail as any).message || JSON.stringify(detail);
  }

  if (typeof anyErr?.message === 'string') {
    return anyErr.message;
  }

  return defaultMessage;
}
