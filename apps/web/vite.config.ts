/// <reference types="node" />
import { fileURLToPath } from 'node:url'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'

const here = (relative: string) => fileURLToPath(new URL(relative, import.meta.url))

// The API this talks to is the local backend and nothing else. There is no
// other origin in the configuration, in development or in a build.
const LOCAL_API = 'http://127.0.0.1:8765'

export default defineConfig({
  plugins: [react()],
  server: {
    host: '127.0.0.1',
    port: 5173,
    strictPort: true,
    proxy: {
      '/api': { target: LOCAL_API, changeOrigin: false }
    }
  },
  build: {
    outDir: 'dist',
    sourcemap: false,
    rollupOptions: {
      // The service worker is a second entry so it lands at the site root and
      // can claim the whole scope. It shares no module with the app bundle, so
      // rollup emits it self-contained.
      input: { main: here('./index.html'), sw: here('./src/sw/sw.ts') },
      output: {
        entryFileNames: (chunk) => (chunk.name === 'sw' ? 'sw.js' : 'assets/[name]-[hash].js'),
        chunkFileNames: 'assets/[name]-[hash].js',
        assetFileNames: 'assets/[name]-[hash][extname]'
      }
    }
  },
  test: {
    environment: 'jsdom',
    setupFiles: ['./tests/setup.ts'],
    include: ['tests/**/*.test.ts', 'tests/**/*.test.tsx'],
    restoreMocks: true,
    // Node 25+ defines its own experimental Web Storage globals, and without
    // `--localstorage-file` its `localStorage` is `undefined`. Vitest copies
    // that global over jsdom's, and every test that touches storage dies on
    // it. Turning the Node feature off for the test workers restores jsdom's.
    poolOptions: {
      forks: { execArgv: ['--no-experimental-webstorage'] }
    }
  }
})
