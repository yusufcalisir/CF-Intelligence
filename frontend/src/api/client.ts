import axios from 'axios';

const API_BASE = import.meta.env.VITE_API_URL ?? '';

let _sessionEpoch = 1;
let _sessionAbortController: AbortController | null =
  typeof AbortController !== 'undefined' ? new AbortController() : null;

const SESSION_SENSITIVE_STORAGE_PREFIXES = [
  'cfi_copilot_case_',
  'cfi_case_warning_',
  'cfi_cases_',
  'cfi_alerts_',
  'cfi_token',
  'cfi_tenant_id',
  'cfi_session_user',
  'cfi_refresh_token',
];

function isAuthTransitionEndpoint(url?: string): boolean {
  if (!url) return false;
  return url.includes('/api/v1/auth/login') || url.includes('/api/v1/auth/logout');
}

export function getSessionEpoch(): number {
  return _sessionEpoch;
}

/**
 * Purges sensitive institutional browser state from sessionStorage (Copilot SAR narratives,
 * case warnings, case/alert filter state, and ephemeral session keys) and optionally
 * clears active authentication credentials from localStorage on sign-out.
 */
export function purgeInstitutionalBrowserState(options?: { preserveAuthCredentials?: boolean }): void {
  if (typeof window === 'undefined') return;
  try {
    const keysToRemove: string[] = [];
    for (let i = 0; i < window.sessionStorage.length; i += 1) {
      const key = window.sessionStorage.key(i);
      if (
        key &&
        (key.startsWith('cfi_') ||
          SESSION_SENSITIVE_STORAGE_PREFIXES.some((prefix) => key.startsWith(prefix)))
      ) {
        keysToRemove.push(key);
      }
    }
    for (const key of keysToRemove) {
      window.sessionStorage.removeItem(key);
    }
  } catch {
    // ignore storage errors
  }

  if (!options?.preserveAuthCredentials) {
    try {
      window.localStorage.removeItem('cfi_token');
      window.localStorage.removeItem('cfi_refresh_token');
      window.localStorage.removeItem('cfi_tenant_id');
      window.localStorage.removeItem('cfi_session_user');
    } catch {
      // ignore storage errors
    }
  }
}

/**
 * Advances the institutional session epoch, aborts all in-flight HTTP requests bound to the
 * superseded session, cancels and clears the React Query cache, and purges sessionStorage
 * investigation artifacts so that switching principals or signing out cannot leak stale data.
 */
export async function advanceInstitutionalSessionEpoch(
  queryClient?: { cancelQueries?: () => Promise<void>; clear: () => void } | null,
  options?: { preserveAuthCredentials?: boolean },
): Promise<number> {
  _sessionEpoch += 1;
  if (_sessionAbortController) {
    try {
      _sessionAbortController.abort('INSTITUTIONAL_SESSION_BOUNDARY_TRANSITION');
    } catch {
      // ignore abort error
    }
  }
  _sessionAbortController = typeof AbortController !== 'undefined' ? new AbortController() : null;

  purgeInstitutionalBrowserState(options);

  if (queryClient) {
    try {
      if (typeof queryClient.cancelQueries === 'function') {
        await queryClient.cancelQueries();
      }
    } catch {
      // ignore query cancellation error
    }
    queryClient.clear();
  }
  return _sessionEpoch;
}

export const apiClient = axios.create({
  baseURL: API_BASE,
  headers: { 'Content-Type': 'application/json' },
  timeout: 30000,
});

apiClient.interceptors.request.use((config) => {
  const cfg = config as typeof config & {
    __cfiSessionEpoch?: number;
    __cfiSessionTenant?: string;
  };
  cfg.__cfiSessionEpoch = _sessionEpoch;

  if (typeof window !== 'undefined') {
    const token = localStorage.getItem('cfi_token') || sessionStorage.getItem('cfi_token');
    if (token && !config.headers.Authorization) {
      config.headers.Authorization = `Bearer ${token}`;
    }
    const tenantId = localStorage.getItem('cfi_tenant_id') || sessionStorage.getItem('cfi_tenant_id');
    cfg.__cfiSessionTenant = tenantId || 'default';
    if (tenantId && !config.headers['X-Tenant-ID']) {
      config.headers['X-Tenant-ID'] = tenantId;
    }
  } else {
    cfg.__cfiSessionTenant = 'default';
  }

  if (!isAuthTransitionEndpoint(config.url) && _sessionAbortController) {
    if (!config.signal) {
      config.signal = _sessionAbortController.signal;
    }
  }

  return config;
});

apiClient.interceptors.response.use(
  (response) => {
    const cfg = response.config as typeof response.config & {
      __cfiSessionEpoch?: number;
    };
    if (
      !isAuthTransitionEndpoint(response.config?.url) &&
      typeof cfg?.__cfiSessionEpoch === 'number' &&
      cfg.__cfiSessionEpoch !== _sessionEpoch
    ) {
      return Promise.reject(
        new axios.CanceledError('Discarded response from superseded institutional session epoch.'),
      );
    }
    return response;
  },
  (error) => {
    if (axios.isCancel(error)) {
      // Normal React / TanStack Query query cancellation or session epoch transition
      return Promise.reject(error);
    }
    const cfg = error?.config as { __cfiSessionEpoch?: number; url?: string } | undefined;
    if (
      !isAuthTransitionEndpoint(cfg?.url) &&
      typeof cfg?.__cfiSessionEpoch === 'number' &&
      cfg.__cfiSessionEpoch !== _sessionEpoch
    ) {
      return Promise.reject(
        new axios.CanceledError('Discarded error from superseded institutional session epoch.'),
      );
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

export function switchActiveTenant(
  queryClient: { cancelQueries?: () => Promise<void>; clear: () => void },
  newTenantId: string,
): void {
  void advanceInstitutionalSessionEpoch(queryClient, { preserveAuthCredentials: true });
  setClientTenant(newTenantId);
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
