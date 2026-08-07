import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import path from 'path';

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  build: {
    chunkSizeWarningLimit: 600,
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (id.includes('node_modules')) {
            const normalizedPath = id.replace(/\\/g, '/');
            if (normalizedPath.includes('/node_modules/three/') || normalizedPath.includes('/node_modules/recharts/')) {
              return 'vendor-charts';
            }
            if (normalizedPath.includes('/node_modules/cytoscape/') || normalizedPath.includes('/node_modules/@xyflow/')) {
              return 'vendor-graph';
            }
            if (
              normalizedPath.includes('/node_modules/react/') ||
              normalizedPath.includes('/node_modules/react-dom/') ||
              normalizedPath.includes('/node_modules/react-router/') ||
              normalizedPath.includes('/node_modules/react-router-dom/')
            ) {
              return 'vendor-react';
            }
            if (normalizedPath.includes('/node_modules/framer-motion/') || normalizedPath.includes('/node_modules/lucide-react/')) {
              return 'vendor-ui';
            }
          }
        },
      },
    },
  },
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
});
