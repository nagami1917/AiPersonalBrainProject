/**
 * One definition of "what path do we send to FastAPI".
 *
 * ## The single fact this file exists to hold
 *
 * The API is versioned at `/api/v1`. That string appears **once**, in
 * `API_PATH_PREFIX`, and everything else is derived from it: the browser URL,
 * the proxy target, and the dev-server route. A previous design mounted FastAPI
 * at `/v1` and let the browser call `/api/v1`, with a proxy translating between
 * the two. Two spellings of one path meant every log line, doc example and test
 * URL had to know which side of the proxy it was on — and a mismatch between the
 * halves was invisible until it 404'd.
 *
 * ## Resource paths, not versioned paths
 *
 * Callers express paths *relative to the version*:
 *
 *   `/workspaces/{id}/notes`   not   `/api/v1/workspaces/{id}/notes`
 *
 * `client.ts` prepends the prefix. This is deliberate:
 *
 *   - a caller cannot accidentally produce `/api/v1/api/v1/...`;
 *   - bumping to `/api/v2` is a change in this file, not 40 call sites;
 *   - the proxy and the client read the same constant, so they cannot disagree.
 *
 * `joinOrigin` still tolerates a caller that *does* pass a full versioned path,
 * because `PUBLIC_API_BASE_URL` is operator-supplied and someone will paste
 * `https://host/api/v1` into it.
 */

/** `/api` — the routing namespace. Changing this moves the API, not the version. */
export const API_NAMESPACE = '/api';

/** `v1` — the version segment, without slashes. */
export const API_VERSION = 'v1';

/** `/api/v1` — the full browser-facing and upstream prefix. */
export const API_PATH_PREFIX = `${API_NAMESPACE}/${API_VERSION}`;

/** True when `path` (a URL pathname) is addressed to this API. */
export function isApiPath(path: string): boolean {
  return path === API_PATH_PREFIX || path.startsWith(`${API_PATH_PREFIX}/`);
}

/**
 * Prefix a resource path.
 *
 * `'/workspaces/x/notes'` → `'/api/v1/workspaces/x/notes'`
 *
 * Idempotent: a path that already carries the prefix is returned unchanged, so a
 * caller that passes a full path (a redirect target, a value from the server)
 * does not get it twice.
 */
export function apiPath(path: string): string {
  if (isApiPath(path)) return path;
  const withSlash = path.startsWith('/') ? path : `/${path}`;
  return `${API_PATH_PREFIX}${withSlash}`;
}

/**
 * Translate a browser path into the path FastAPI serves.
 *
 * Since both are `/api/v1`, this is the identity for a correctly-prefixed path.
 * It exists rather than being deleted so the proxy has one named place to state
 * that fact, and because a path outside the prefix must be returned unchanged
 * (the proxy forwards it as-is rather than guessing).
 */
export function toUpstreamPath(path: string): string {
  return isApiPath(path) ? path : path;
}

/**
 * Join a configured origin with a path, collapsing a duplicate prefix.
 *
 * Used only when `PUBLIC_API_BASE_URL` is an absolute URL, and always with a
 * path that already carries the prefix. Handles the three plausible operator
 * inputs without producing `/api/v1/api/v1/...`:
 *
 *   `https://host`          + `/api/v1/notes`   → `https://host/api/v1/notes`
 *   `https://host/api/v1`   + `/api/v1/notes`   → `https://host/api/v1/notes`
 *   `https://host/api`      + `/api/v1/notes`   → `https://host/api/v1/notes`
 */
export function joinOrigin(origin: string, path: string): string {
  const trimmed = origin.replace(/\/+$/, '');

  if (trimmed.endsWith(API_PATH_PREFIX)) {
    return `${trimmed}${path.slice(API_PATH_PREFIX.length)}`;
  }
  if (trimmed.endsWith(API_NAMESPACE)) {
    return `${trimmed}${path.slice(API_NAMESPACE.length)}`;
  }
  // No auto-prefixing: `client.ts` calls `apiPath()` before joining, so by the
  // time a path reaches here it is already versioned. Prefixing again would be
  // the double-prefix bug this function exists to avoid, and it would mangle a
  // non-API path (`/health`) into one that does not exist.
  return `${trimmed}${path.startsWith('/') ? path : `/${path}`}`;
}
