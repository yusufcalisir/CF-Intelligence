import axios from 'axios';

const API_BASE = import.meta.env.VITE_API_URL ?? '';

export const apiClient = axios.create({
  baseURL: API_BASE,
  headers: { 'Content-Type': 'application/json' },
  timeout: 12000,
});

apiClient.interceptors.response.use(
  (response) => response,
  (error) => {
    if (axios.isCancel(error) || error.code === 'ECONNABORTED' || error.message?.includes('timeout')) {
      console.warn('[API Warning] Request timed out, using cached/fallback state:', error.config?.url);
    } else {
      console.warn('[API Warning]', error.response?.data ?? error.message);
    }
    return Promise.reject(error);
  },
);
