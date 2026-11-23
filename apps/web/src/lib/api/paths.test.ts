import { describe, expect, it } from 'vitest';

import {
  API_NAMESPACE,
  API_PATH_PREFIX,
  API_VERSION,
  apiPath,
  isApiPath,
  joinOrigin,
  toUpstreamPath
} from './paths';

/**
 * The API is versioned at `/api/v1`, defined once. These tests pin the two
 * properties that make that single definition worth having:
 *
 *   1. Callers express resource paths; the prefix is applied for them.
 *   2. Every shape of operator-supplied base URL joins without duplicating the
 *      prefix.
 *
 * They also pin the identity that `toUpstreamPath` now is. That is not a
 * tautology worth testing for its own sake — it is the bug that used to exist
 * (FastAPI at `/v1`, browser at `/api/v1`, a proxy translating) and the test
 * exists so a future "clever" rewrite has to argue with it.
 */

describe('API prefix', () => {
  it('is built from a namespace and a version, defined once', () => {
    expect(API_NAMESPACE).toBe('/api');
    expect(API_VERSION).toBe('v1');
    expect(API_PATH_PREFIX).toBe('/api/v1');
  });

  it('composes rather than hardcoding the composed string', () => {
    expect(API_PATH_PREFIX).toBe(`${API_NAMESPACE}/${API_VERSION}`);
  });
});

describe('apiPath', () => {
  it('prefixes a resource path', () => {
    expect(apiPath('/workspaces/x/notes')).toBe('/api/v1/workspaces/x/notes');
  });

  it('tolerates a path without a leading slash', () => {
    expect(apiPath('workspaces')).toBe('/api/v1/workspaces');
  });

  it('is idempotent, so a full path is never doubled', () => {
    // The failure mode this prevents: /api/v1/api/v1/... and a 404 on every
    // request. Callers sometimes pass a path that came back from the server.
    const full = '/api/v1/notes';
    expect(apiPath(full)).toBe(full);
    expect(apiPath(apiPath('/notes'))).toBe('/api/v1/notes');
  });

  it('prefixes the bare root', () => {
    expect(apiPath('/')).toBe('/api/v1/');
  });
});

describe('isApiPath', () => {
  it('recognises the prefix and anything under it', () => {
    expect(isApiPath('/api/v1')).toBe(true);
    expect(isApiPath('/api/v1/notes')).toBe(true);
  });

  it('does not match a lookalike prefix', () => {
    // /api/v10 is a different version and must not be treated as ours, and
    // /apix is not ours at all. A bare startsWith would get both wrong.
    expect(isApiPath('/api/v10/notes')).toBe(false);
    expect(isApiPath('/apix')).toBe(false);
    expect(isApiPath('/notes')).toBe(false);
    expect(isApiPath('/')).toBe(false);
  });
});

describe('toUpstreamPath', () => {
  it('is the identity, because FastAPI also mounts at /api/v1', () => {
    // The old design stripped /api here. Keeping a named function (rather than
    // deleting it) documents that the translation is deliberate.
    expect(toUpstreamPath('/api/v1/notes')).toBe('/api/v1/notes');
    expect(toUpstreamPath('/api/v1/notes?limit=5&cursor=x')).toBe(
      '/api/v1/notes?limit=5&cursor=x'
    );
  });

  it('returns a foreign path unchanged rather than guessing', () => {
    expect(toUpstreamPath('/health')).toBe('/health');
  });
});

describe('joinOrigin', () => {
  it('joins a bare origin', () => {
    expect(joinOrigin('https://api.example.com', '/api/v1/notes')).toBe(
      'https://api.example.com/api/v1/notes'
    );
  });

  it('collapses a base that already carries /api/v1', () => {
    // The spec's API_BASE_URL form. Pasting it must not produce /api/v1/api/v1.
    expect(joinOrigin('https://api.example.com/api/v1', '/api/v1/notes')).toBe(
      'https://api.example.com/api/v1/notes'
    );
  });

  it('collapses a base that carries only /api', () => {
    expect(joinOrigin('https://api.example.com/api', '/api/v1/notes')).toBe(
      'https://api.example.com/api/v1/notes'
    );
  });

  it('strips trailing slashes from the origin', () => {
    expect(joinOrigin('https://api.example.com///', '/api/v1/notes')).toBe(
      'https://api.example.com/api/v1/notes'
    );
  });

  it('does not prefix — callers apply apiPath() first', () => {
    // Prefixing here as well would give /api/v1/api/v1/... for every request.
    expect(joinOrigin('https://api.example.com', '/notes')).toBe(
      'https://api.example.com/notes'
    );
  });

  it('composes with apiPath the way client.ts uses it', () => {
    expect(joinOrigin('https://api.example.com', apiPath('/workspaces/x/notes'))).toBe(
      'https://api.example.com/api/v1/workspaces/x/notes'
    );
  });

  it('keeps a non-API path that the API host also serves', () => {
    expect(joinOrigin('https://api.example.com', '/health')).toBe(
      'https://api.example.com/health'
    );
  });
});
