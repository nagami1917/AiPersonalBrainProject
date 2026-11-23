/**
 * One definition of "what path do we send to FastAPI", used by both the
 * browser client and the dev proxy.
 *
 * Two callers with different inputs:
 *
 *   - the browser passes `/v1/workspaces/{id}/notes` and needs it relative to
 *     the API base;
 *   - the dev proxy receives `/api/v1/workspaces/{id}/notes` from the browser and
 *     needs `/v1/workspaces/{id}/notes` to forward upstream.
 *
 * Rather than duplicating the two remappings, both come from `API_PREFIX`:
 * change the prefix in one place and the client and the proxy stay in step.
 * A mismatch between them is the kind of bug that only shows up in dev, which
 * is exactly when it is cheapest to prevent.
 */

/**
 * Everything before `/v1`: the segment the browser uses for the API *suffix*.
 *
 * Callers already build versioned paths (`wsPath()` returns
 * `/v1/workspaces/{id}/…`), so the prefix must not carry the version or the
 * relative URL becomes `/api/v1/v1/…`. Keeping the split explicit here is what
 * makes that impossible to get wrong at a call site.
 */
export const API_PREFIX = '/api';

/** The mount point FastAPI uses for every router — never stripped, never added. */
export const API_VERSION_PREFIX = '/v1';

/** `/api` + `/v1` — the full browser-facing prefix, for matching and display. */
export const API_PATH_PREFIX = `${API_PREFIX}${API_VERSION_PREFIX}`;

/** True when `path` (a URL path) is addressed to the API. */
export function isApiPath(path: string): boolean {
  return path === API_PATH_PREFIX || path.startsWith(`${API_PATH_PREFIX}/`);
}

/**
 * Translate a browser API path into the upstream path.
 *
 * `/api/v1/notes?x=1` → `/v1/notes?x=1`: strip `/api`, keep `/v1`. A path that is
 * not under the prefix is returned unchanged, so a caller that already has an
 * upstream path does not need to check first.
 */
export function toUpstreamPath(path: string): string {
  if (!isApiPath(path)) return path;
  return path.slice(API_PREFIX.length);
}

/**
 * Join the configured origin with a browser API path.
 *
 * Only used when `PUBLIC_API_BASE_URL` is an **absolute** URL, which is the
 * explicitly-configured case and the one that cannot miss CORS.
 */
export function joinOrigin(origin: string, path: string): string {
  const trimmed = origin.replace(/\/+$/, '');
  if (trimmed.endsWith(API_PATH_PREFIX)) {
    // The operator pasted the spec's full `.../api/v1` form. Treated as a base
    // URL rather than an error, so the two plausible readings of
    // `API_BASE_URL=http://host:8000/api/v1` both work instead of one producing
    // `/api/v1/api/v1/…` and a 404 on every request.
    return `${trimmed}${path.slice(API_PATH_PREFIX.length)}`;
  }
  if (trimmed.endsWith(API_PREFIX)) {
    return `${trimmed}${path.slice(API_PREFIX.length)}`;
  }
  return `${trimmed}${path}`;
}
