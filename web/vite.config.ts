import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { VitePWA } from 'vite-plugin-pwa'

// https://vite.dev/config/
export default defineConfig({
  plugins: [
    react(),
    // Offline shell: card shows have terrible wifi, and History/Settings are
    // fully local — the installed app must open with zero signal.
    VitePWA({
      registerType: 'autoUpdate',
      // Static assets outside the build graph that still need precaching.
      includeAssets: ['icon.svg', 'apple-touch-icon.png'],
      // The plugin owns the manifest (generates manifest.webmanifest and
      // injects the <link> into index.html).
      manifest: {
        name: 'Card Scanner',
        short_name: 'CardScan',
        description: 'Scan a sports card, get a grade estimate and a price verdict.',
        start_url: '/',
        display: 'standalone',
        theme_color: '#1e293b',
        background_color: '#0f172a',
        icons: [
          { src: '/icon.svg', sizes: 'any', type: 'image/svg+xml', purpose: 'any' },
          { src: '/icon-512.png', sizes: '512x512', type: 'image/png', purpose: 'any' },
        ],
      },
      workbox: {
        globPatterns: ['**/*.{js,css,html,svg,png}'],
        // App-shell routing: unknown navigations serve the cached index.html.
        navigateFallback: 'index.html',
        navigateFallbackDenylist: [/^\/api\//],
        // Scans are paid, personalized calls — never serve one from cache.
        // (This rule matches GETs like /api/health; POST /api/scan is safe
        // because workbox never caches non-GET requests at all.)
        // Cross-origin requests are untouched (workbox default: no caching).
        runtimeCaching: [
          {
            urlPattern: ({ url, sameOrigin }) => sameOrigin && url.pathname.startsWith('/api/'),
            handler: 'NetworkOnly',
          },
        ],
      },
    }),
  ],
  server: {
    // Dev only: forward API calls to the FastAPI server so the app works
    // on http://localhost:5173 without setting VITE_API_URL.
    proxy: {
      '/api': 'http://localhost:8000',
    },
  },
})
