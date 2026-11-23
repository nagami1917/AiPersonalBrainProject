import { sveltekit } from '@sveltejs/kit/vite';
import { defineConfig, loadEnv } from 'vite';

// `process` is available here in Node, but @types/node is not a dependency and
// adding one for a single call is not worth it. Declared narrowly instead.
declare const process: { cwd(): string };

export default defineConfig(({ mode }) => {
  /**
   * Resolve where to forward browser requests for `/api/v1`.
   *
   * `loadEnv(mode, cwd, '')` with an empty prefix reads *every* variable the
   * `.env` files define, not just `PUBLIC_*`. That is deliberate and safe here:
   * this file runs in Node at dev-server startup and its values never enter the
   * browser bundle. It is also necessary, because `API_BASE_URL` — the spec's
   * name — is not `PUBLIC_`-prefixed, so the app itself cannot read it (see
   * `src/lib/utils/env.ts`); the proxy can, and that is how the spec's spelling
   * still works.
   *
   * The trailing `/api/v1` is stripped because the proxy forwards the *upstream*
   * path: `API_BASE_URL` says where the browser sends requests, not where FastAPI
   * mounts them.
   */
  const env = loadEnv(mode, process.cwd(), '');
  const configured = (env.PUBLIC_API_BASE_URL || env.API_BASE_URL || '').trim();
  const target = (configured || 'http://127.0.0.1:8000')
    .replace(/\/+$/, '')
    .replace(/\/api\/v1$/, '');

  return {
    plugins: [sveltekit()],
    server: {
      host: '0.0.0.0',
      port: 5173,
      strictPort: true,
      // Served behind a dynamic preview proxy host, so no host allowlist.
      // Restricting `allowedHosts` here breaks the live preview in hosted envs.
      allowedHosts: true,
      fs: { allow: ['..'] },
      proxy: {
        // Same-origin in development: the browser calls `/api/v1/...` and Vite
        // forwards to FastAPI. This is why the frontend needs no CORS setup for
        // local work, and why the default `PUBLIC_API_BASE_URL` is empty rather
        // than `http://localhost:8000` — a deployed browser cannot reach
        // localhost, and a relative path is the one configuration that is
        // correct in both places.
        '/api/v1': {
          target,
          changeOrigin: true,
          // Strip the public prefix; FastAPI mounts everything at `/v1`.
          rewrite: (path) => path.replace(/^\/api\/v1/, '/v1'),
          // Streaming chat is SSE. A buffering proxy turns a token-by-token
          // answer into one late blob, which reads as a hang.
          configure: (proxy) => {
            proxy.on('proxyRes', (proxyRes) => {
              if (proxyRes.headers['content-type']?.includes('text/event-stream')) {
                proxyRes.headers['cache-control'] = 'no-cache, no-transform';
                proxyRes.headers['x-accel-buffering'] = 'no';
              }
            });
          }
        }
      }
    }
  };
});
