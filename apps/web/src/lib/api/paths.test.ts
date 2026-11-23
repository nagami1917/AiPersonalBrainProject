import { describe, expect, it } from 'vitest';

import {
  API_PATH_PREFIX,
  API_PREFIX,
  API_VERSION_PREFIX,
  joinOrigin,
  isApiPath,
  toUpstreamPath
} from './paths';

/**
 * These tests exist because getting the base URL wrong is invisible in the way
 * that costs the most: the app loads, the shell renders, and every data request
 * fails with a network error that looks like the backend is down.
 *
 * The three shapes that must all work:
 *
 *   relative       no base configured → `/api/v1/...` on this origin
 *   absolute       `https://api.host`  → joined directly
 *   spec's form    `https://host/api/v1` → prefix not duplicated
 */
describe('API prefix', () => {
  it('separates the browser segment from the versioned mount point', () => {
    // Callers already build `/v1/...` paths, so the prefix must be `/api` alone.
    // Folding the version in here produces `/api/v1/v1/notes`.
    expect(API_PREFIX).toBe('/api');
    expect(API_VERSION_PREFIX).toBe('/v1');
    expect(API_PATH_PREFIX).toBe('/api/v1');
  });
});

describe('isApiPath', () => {
  it('matches the prefix itself and anything beneath it', () => {
    expect(isApiPath('/api/v1')).toBe(true);
    expect(isApiPath('/api/v1/notes')).toBe(true);
    expect(isApiPath('/api/v1/workspaces/x/chat/stream')).toBe(true);
  });

  it('does not match a lookalike prefix', () => {
    // `/api/v10` is not `/api/v1`; a bare startsWith would forward it upstream.
    expect(isApiPath('/api/v10/notes')).toBe(false);
    expect(isApiPath('/api/notes')).toBe(false);
    expect(isApiPath('/notes')).toBe(false);
  });
});

describe('toUpstreamPath', () => {
  it('strips the public prefix, because FastAPI mounts at /v1', () => {
    expect(toUpstreamPath('/api/v1/notes')).toBe('/v1/notes');
    expect(toUpstreamPath('/api/v1/workspaces/abc/chat/stream')).toBe(
      '/v1/workspaces/abc/chat/stream'
    );
  });

  it('preserves a query string', () => {
    expect(toUpstreamPath('/api/v1/notes?limit=5&cursor=x')).toBe('/v1/notes?limit=5&cursor=x');
  });

  it('leaves an already-upstream path alone', () => {
    expect(toUpstreamPath('/v1/notes')).toBe('/v1/notes');
  });

  it('does not strip a lookalike prefix', () => {
    expect(toUpstreamPath('/api/v10/notes')).toBe('/api/v10/notes');
  });
});

describe('joinOrigin', () => {
  it('joins a bare origin', () => {
    expect(joinOrigin('https://api.example.com', '/v1/notes')).toBe(
      'https://api.example.com/v1/notes'
    );
  });

  it('joins an origin with a path prefix', () => {
    expect(joinOrigin('https://example.com/backend', '/v1/notes')).toBe(
      'https://example.com/backend/v1/notes'
    );
  });

  it('does not duplicate the prefix when the base already ends with /api/v1', () => {
    // The spec's `API_BASE_URL=http://localhost:8000/api/v1` form. Repeating the
    // prefix here would produce `/api/v1/api/v1/notes` and a 404 on every call.
    expect(joinOrigin('http://localhost:8000/api/v1', '/api/v1/notes')).toBe(
      'http://localhost:8000/api/v1/notes'
    );
  });

  it('tolerates a trailing slash on the origin', () => {
    expect(joinOrigin('https://api.example.com/', '/v1/notes')).toBe(
      'https://api.example.com/v1/notes'
    );
    expect(joinOrigin('https://api.example.com///', '/v1/notes')).toBe(
      'https://api.example.com/v1/notes'
    );
  });

  it('keeps an http origin intact for local work', () => {
    expect(joinOrigin('http://127.0.0.1:8000', '/v1/meta')).toBe(
      'http://127.0.0.1:8000/v1/meta'
    );
  });
});

describe('what a browser actually requests', () => {
  /** Mirrors `apiUrl()` in client.ts — the two must agree. */
  const url = (base: string, path: string) => (base ? joinOrigin(base, path) : `${API_PREFIX}${path}`);

  it('uses a relative path when nothing is configured', () => {
    // `/api` + the caller's `/v1/...` path. No doubled version segment.
    expect(url('', '/v1/workspaces/x/notes')).toBe('/api/v1/workspaces/x/notes');
  });

  it('produces the same target through the dev proxy', () => {
    // The relative request above is rewritten by the vite proxy to the upstream
    // path, which is what an absolute base hits directly. Same endpoint, so
    // switching between the two configurations cannot change behaviour.
    const relative = url('', '/v1/workspaces/x/notes');
    expect(toUpstreamPath(relative)).toBe('/v1/workspaces/x/notes');
    expect(url('http://127.0.0.1:8000', '/v1/workspaces/x/notes')).toBe(
      'http://127.0.0.1:8000/v1/workspaces/x/notes'
    );
  });

  it('never emits a localhost URL when no base is configured', () => {
    // The failure this guards: a deployed bundle calling 127.0.0.1, which only
    // ever succeeds on the developer's own machine.
    expect(url('', '/v1/meta')).not.toMatch(/localhost|127\.0\.0\.1/);
  });
});
