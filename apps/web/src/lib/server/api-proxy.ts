/**
 * Pure helpers for the same-origin API proxy (`src/routes/api/v1/[...path]/+server.ts`).
 *
 * ## Why this exists at all
 *
 * The browser calls `/api/v1/...` on its own origin. Something has to forward
 * that to FastAPI. Until this module existed,
 * the only thing that did the forwarding was `server.proxy` in `vite.config.ts`
 * — a **dev-server** feature. It had no counterpart in the Vercel build, so a
 * deployed app asked SvelteKit's router for `/api/v1/meta`, got a 404, and the
 * documented "same-origin, no CORS" default silently produced a blank page.
 *
 * The fix is one proxy used in *every* environment rather than two mechanisms
 * that can drift apart. This module holds the decisions — where to forward,
 * which headers survive the hop, what a failure looks like — so they can be
 * unit-tested without a running server or a network.
 *
 * ## Deliberately not here
 *
 * No auth logic. The proxy forwards `Authorization` untouched and FastAPI
 * verifies the JWT as it always has. A proxy that also tried to validate
 * tokens would be a second, weaker copy of the security boundary (rule 1/8:
 * the browser's authority is the anon key plus RLS; the API's is the JWT).
 */

import { API_NAMESPACE, API_PATH_PREFIX } from '$lib/api/paths';

/**
 * Headers that describe *one* connection and must never be relayed to another,
 * per RFC 9110 §7.6.1. Forwarding these produces subtly broken responses —
 * most commonly `content-encoding: gzip` on a body Node has already decoded,
 * which the browser then fails to parse.
 */
export const HOP_BY_HOP_HEADERS = new Set([
  'connection',
  'keep-alive',
  'proxy-authenticate',
  'proxy-authorization',
  'te',
  'trailer',
  'transfer-encoding',
  'upgrade',
  // Managed by the fetch implementation, not by us.
  'host',
  'content-length'
]);

/** Where the API actually lives, and whether that was configured or inferred. */
export type Upstream = {
  /** Origin plus any base path, no trailing slash. e.g. `https://nexus-api.fly.dev` */
  base: string;
  /** Why this value was chosen — surfaced in errors and `GET /api/v1/__health`. */
  source: 'api_base_url' | 'public_api_base_url' | 'development_default';
};

/** Environment shape this module needs. Passed in, so tests need no `$env`. */
export type ProxyEnv = {
  /** Server-only target. The spec's name, and the canonical one here. */
  apiBaseUrl?: string;
  /** The browser-facing base URL. Only consulted when it is an absolute origin. */
  publicApiBaseUrl?: string;
  /** `production` disables the localhost fallback below. */
  environment?: string;
  /** Vite sets `development`; used only for the localhost fallback message. */
  dev?: boolean;
};

/**
 * Decide where to forward.
 *
 * Precedence, highest first:
 *
 *   1. `API_BASE_URL` — server-only, so it can point at an internal address the
 *      browser cannot reach. This is the value a deployment should set.
 *   2. `PUBLIC_API_BASE_URL`, but only if it is **absolute**. When it is
 *      absolute the browser talks to FastAPI directly and never reaches this
 *      proxy; honouring it here anyway keeps the two shapes consistent if the
 *      client is ever changed to route through the proxy.
 *   3. `http://127.0.0.1:8000` — local development only.
 *
 * Returns `null` when there is nothing sane to use, i.e. production with no
 * configuration. A caller that returns 503 with an explicit message is far
 * better than one that forwards to localhost and reports a connection refused,
 * because the operator can read the first and not the second.
 */
export function resolveUpstream(env: ProxyEnv): Upstream | null {
  const apiBaseUrl = (env.apiBaseUrl ?? '').trim();
  if (apiBaseUrl) {
    return { base: stripTrailingSlashes(apiBaseUrl), source: 'api_base_url' };
  }

  const publicBaseUrl = (env.publicApiBaseUrl ?? '').trim();
  if (publicBaseUrl && /^https?:\/\//i.test(publicBaseUrl)) {
    return { base: stripTrailingSlashes(publicBaseUrl), source: 'public_api_base_url' };
  }

  // A relative or empty PUBLIC_API_BASE_URL means "same origin", which says
  // nothing about where FastAPI is — that is what API_BASE_URL is for.
  if (env.environment === 'production') {
    return null;
  }

  return { base: 'http://127.0.0.1:8000', source: 'development_default' };
}

/**
 * Build the upstream URL for a browser request.
 *
 * `/api/v1/workspaces/x/notes?a=1` → `https://host/api/v1/workspaces/x/notes?a=1`
 *
 * The path is **not rewritten**. FastAPI serves `/api/v1` and the browser calls
 * `/api/v1`, so forwarding is the identity — the earlier design mounted FastAPI
 * at `/v1` and stripped the `/api` segment here, which was a second place for
 * the two prefixes to disagree.
 *
 * The one transformation left is on the *base*: an operator may set
 * `API_BASE_URL` to `https://host`, `https://host/api`, or the full
 * `https://host/api/v1`. All three mean the same origin, so the API suffix is
 * collapsed and then re-applied by the incoming path. That keeps
 * `https://host/api/v1` from becoming `https://host/api/v1/api/v1/...`.
 */
export function buildUpstreamUrl(upstream: Upstream, pathname: string, search = ''): string {
  const base = collapseApiSuffix(upstream.base);
  return `${base}${normalisePath(pathname)}${search}`;
}

/**
 * The path to forward.
 *
 * An empty path or `/` is the route root — `/api/v1` itself — which must stay
 * `/api/v1` rather than becoming `/`, because `/` is FastAPI's documentation
 * page and not an API endpoint.
 */
function normalisePath(pathname: string): string {
  if (!pathname || pathname === '/') {
    return API_PATH_PREFIX;
  }
  return pathname.startsWith('/') ? pathname : `/${pathname}`;
}

/**
 * `https://host/api/v1` and `https://host/api` both reduce to `https://host`,
 * because the versioned prefix is re-applied from the incoming request path.
 */
function collapseApiSuffix(base: string): string {
  const trimmed = stripTrailingSlashes(base);
  if (trimmed.toLowerCase().endsWith(API_PATH_PREFIX)) {
    return stripTrailingSlashes(trimmed.slice(0, -API_PATH_PREFIX.length));
  }
  if (trimmed.toLowerCase().endsWith(API_NAMESPACE)) {
    return stripTrailingSlashes(trimmed.slice(0, -API_NAMESPACE.length));
  }
  return trimmed;
}

function stripTrailingSlashes(value: string): string {
  return value.replace(/\/+$/, '');
}

/**
 * Copy the request headers that should reach FastAPI.
 *
 * `Authorization` is the one that matters: it is the user's Supabase token and
 * the API's only identity input. `content-type` is needed for JSON bodies.
 * Everything hop-by-hop is dropped.
 */
export function forwardableRequestHeaders(headers: Headers): Headers {
  const forwarded = new Headers();
  headers.forEach((value, key) => {
    if (!HOP_BY_HOP_HEADERS.has(key.toLowerCase())) {
      forwarded.set(key, value);
    }
  });
  // A client-supplied content-length cannot be right after buffering, and
  // fetch sets it from the actual body.
  forwarded.delete('content-length');
  return forwarded;
}

/**
 * Copy the response headers that should reach the browser.
 *
 * Streaming chat (SSE) depends on `content-type: text/event-stream` surviving
 * intact and on nothing buffering it, so the event-stream case gets the two
 * anti-buffering headers as well.
 */
export function forwardableResponseHeaders(headers: Headers): Headers {
  const forwarded = new Headers();
  headers.forEach((value, key) => {
    if (!HOP_BY_HOP_HEADERS.has(key.toLowerCase())) {
      forwarded.set(key, value);
    }
  });

  if (isEventStream(headers)) {
    forwarded.set('cache-control', 'no-cache, no-transform');
    forwarded.set('x-accel-buffering', 'no');
  }
  return forwarded;
}

/** True when a response is a server-sent event stream (the chat answer). */
export function isEventStream(headers: Headers): boolean {
  return (headers.get('content-type') ?? '').toLowerCase().includes('text/event-stream');
}

/**
 * Methods the proxy accepts. `HEAD` is deliberately absent: FastAPI does not
 * serve it and pretending it works hides a 405 until something depends on it.
 */
export const PROXY_METHODS = ['GET', 'POST', 'PATCH', 'PUT', 'DELETE'] as const;

/**
 * The API's error envelope (API.md §1), reproduced so a proxy failure is
 * rendered by the same frontend path as an API failure. `client.ts` reads
 * `error.code`, `error.message` and `error.details`; inventing a different
 * shape here would surface as "Something went wrong" with no cause.
 */
export function proxyErrorBody(code: string, message: string, details: Record<string, unknown> = {}) {
  return { error: { code, message, details } };
}
