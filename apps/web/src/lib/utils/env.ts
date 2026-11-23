/**
 * Public configuration, read once and validated.
 *
 * Only PUBLIC_* values exist here. The service-role key and AI keys live on the
 * backend and must never appear in this file or in Vercel's frontend env
 * (ARCHITECTURE.md §3, SECURITY.md §2).
 *
 * ## Why `API_BASE_URL` alone is not enough
 *
 * The spec's environment block names it `API_BASE_URL`. SvelteKit only exposes
 * `PUBLIC_`-prefixed variables to the browser — `$env/dynamic/public` is typed
 * as ``{ [key: `PUBLIC_${string}`] }`` precisely so that a bare name cannot be
 * relied on — so a frontend that reads only `API_BASE_URL` gets `undefined`.
 *
 * That is the framework enforcing the spec's own rule ("public Supabase
 * credentials may be used by the browser"), so this module does not fight it:
 *
 *   - `PUBLIC_API_BASE_URL` is what the app reads;
 *   - a bare `API_BASE_URL` is read **server-side** by the `/api/v1` proxy route
 *     (`src/routes/api/v1/[...path]/+server.ts`), never bundled, so it is the
 *     upstream for the same-origin shape rather than a browser setting;
 *   - the default with neither set is a relative path, which is the recommended
 *     production shape anyway.
 *
 * `DEPLOYMENT.md` §3 states this table rather than implying the two spellings
 * are interchangeable in the browser.
 *
 * ## Why the default is relative
 *
 * With no API base URL configured the app calls `/api/v1/...` on its own origin,
 * and `vite.config.ts` proxies that to FastAPI in development. That is the
 * documented setup (see DEPLOYMENT.md §3): one origin, so there is no CORS
 * preflight to configure and no `localhost` reachable from a deployed browser.
 * An absolute `PUBLIC_API_BASE_URL` still works — that path is covered by tests,
 * including the spec's `.../api/v1` form.
 */
import { env as publicEnv } from '$env/dynamic/public';

import { API_PATH_PREFIX } from '$lib/api/paths';

function required(name: string, value: string | undefined): string {
  if (!value) {
    throw new Error(`Missing public environment variable: ${name}`);
  }
  return value;
}

/**
 * Where browser requests to `/api/v1/...` should actually go.
 *
 * Returns `''` for the same-origin (proxied) case, so callers can concatenate
 * without a conditional.
 */
function resolveApiBaseUrl(): string {
  const configured = (publicEnv.PUBLIC_API_BASE_URL || '').trim();
  if (!configured) {
    // Same origin: the `/api/v1` route forwards server-side to `API_BASE_URL`.
    // Not an error — it is the default production shape (DEPLOYMENT.md §3.1).
    return '';
  }
  return configured.replace(/\/+$/, '');
}

export const env = {
  supabaseUrl: required('PUBLIC_SUPABASE_URL', publicEnv.PUBLIC_SUPABASE_URL),
  supabaseAnonKey: required('PUBLIC_SUPABASE_ANON_KEY', publicEnv.PUBLIC_SUPABASE_ANON_KEY),
  /** Empty string means "same origin, use the `/api/v1` prefix". */
  apiBaseUrl: resolveApiBaseUrl(),
  /** The prefix browser requests carry, relative to the origin above. */
  apiPrefix: API_PATH_PREFIX
};
