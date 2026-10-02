/// <reference types="node" />
import { fileURLToPath } from 'node:url'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

const here = (relative: string) => fileURLToPath(new URL(relative, import.meta.url))

// The in-chat build (ADR 0014): one entry, one chunk, one stylesheet, so that
// scripts/inline-app.mjs can fold everything into a single HTML document the
// MCP server serves as an app resource. Nothing may be loaded by URL inside
// a host's sandbox, so nothing is.
export default defineConfig({
  plugins: [react()],
  build: {
    outDir: 'dist-app',
    emptyOutDir: true,
    sourcemap: false,
    cssCodeSplit: false,
    modulePreload: false,
    rollupOptions: {
      input: { app: here('./app.html') },
      output: {
        inlineDynamicImports: true,
        entryFileNames: 'app.js',
        assetFileNames: 'app[extname]'
      }
    }
  }
})
