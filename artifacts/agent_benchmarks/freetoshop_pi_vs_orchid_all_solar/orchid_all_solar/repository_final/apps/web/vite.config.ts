import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  server: { port: 5173 },
  preview: { port: 4173 },
  // Module workers (`type: 'module'`) require ES format; IIFE cannot host
  // multi-chunk graphs pulled in by composite/filter/codec dependencies.
  worker: { format: 'es' },
  build: { target: 'es2022', sourcemap: false },
});
