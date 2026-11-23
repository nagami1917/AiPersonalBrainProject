import { sveltekit } from '@sveltejs/kit/vite';
import { defineConfig } from 'vite';

export default defineConfig({
  plugins: [sveltekit()],
  server: {
    host: '0.0.0.0',
    port: 5173,
    strictPort: true,
    // Served behind a dynamic preview proxy host, so no host allowlist.
    // Restricting `allowedHosts` here breaks the live preview in hosted envs.
    allowedHosts: true,
    fs: { allow: ['..'] }
  }
});

/**
 * There is deliberately NO `server.proxy` for `/api/v1` here.
 *
 * It used to live in this file, and that was the bug: a Vite dev-server proxy
 * has no counterpart in the Vercel build, so the same-origin shape worked in
 * development and 404'd in production while the docs called it "the
 * recommended production shape".
 *
 * `/api/v1/*` is now served by `src/routes/api/v1/[...path]/+server.ts`, which
 * runs under `vite dev` *and* as a Vercel function — one mechanism that behaves
 * identically in both, so the two cannot drift apart again. To change where the
 * API lives, set `API_BASE_URL` (see `$lib/server/api-proxy.ts`); nothing about
 * that decision belongs in this file.
 *
 * Using the app's own route also means the dev server no longer needs
 * `loadEnv(…, '')`, which previously pulled *every* variable in `.env` —
 * including secrets — into the dev process. `$env/dynamic/private` exposes
 * server-only values to the route without widening what any other module can
 * read.
 */
