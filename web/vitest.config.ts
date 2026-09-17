import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'jsdom',
    globals: true,
    // Web Storage shim: Node 26's own localStorage global shadows jsdom's
    // and is unusable without --localstorage-file. See src/test/setup.ts.
    setupFiles: ['./src/test/setup.ts'],
  },
})
