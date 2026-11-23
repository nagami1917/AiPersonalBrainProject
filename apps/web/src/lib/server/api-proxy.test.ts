import { describe, expect, it } from 'vitest';

import {
  HOP_BY_HOP_HEADERS,
  PROXY_METHODS,
  buildUpstreamUrl,
  forwardableRequestHeaders,
  forwardableResponseHeaders,
  isEventStream,
  proxyErrorBody,
  resolveUpstream
} from './api-proxy';

/**
 * These cover the decisions the proxy makes, not its I/O.
 *
 * The defect this module exists to prevent was a *routing* decision (dev-only
 * forwarding), so the tests assert routing: which upstream is chosen for a given
 * environment, and what path the browser's `/api/v1/...` becomes upstream. Both
 * are pure functions, so the suite needs no server and no network.
 */

describe('resolveUpstream', () => {
  it('prefers the server-only API_BASE_URL, which the browser never sees', () => {
    const upstream = resolveUpstream({
      apiBaseUrl: 'https://nexus-api.fly.dev',
      publicApiBaseUrl: 'https://ignored.example.com',
      environment: 'production'
    });
    expect(upstream).toEqual({ base: 'https://nexus-api.fly.dev', source: 'api_base_url' });
  });

  it('strips trailing slashes so a joined path cannot contain //', () => {
    const upstream = resolveUpstream({ apiBaseUrl: 'https://api.example.com///' });
    expect(upstream?.base).toBe('https://api.example.com');
    expect(buildUpstreamUrl(upstream!, '/api/v1/meta')).toBe('https://api.example.com/v1/meta');
  });

  it('falls back to an absolute PUBLIC_API_BASE_URL', () => {
    const upstream = resolveUpstream({
      publicApiBaseUrl: 'https://api.example.com',
      environment: 'production'
    });
    expect(upstream).toEqual({ base: 'https://api.example.com', source: 'public_api_base_url' });
  });

  it('ignores a relative PUBLIC_API_BASE_URL, because it says nothing about the API host', () => {
    // "same origin" is a statement about the browser, not a forwarding target.
    const upstream = resolveUpstream({ publicApiBaseUrl: '/api/v1', environment: 'test' });
    expect(upstream?.source).toBe('development_default');
  });

  it('uses localhost only outside production, and names it as a development default', () => {
    expect(resolveUpstream({ environment: 'local' })).toEqual({
      base: 'http://127.0.0.1:8000',
      source: 'development_default'
    });
    expect(resolveUpstream({ environment: 'test' })?.source).toBe('development_default');
  });

  it('returns null in production with nothing configured rather than silently using localhost', () => {
    // The alternative is a deployed server attempting 127.0.0.1 — an
    // ECONNREFUSED the operator cannot diagnose from the response.
    expect(resolveUpstream({ environment: 'production' })).toBeNull();
    expect(
      resolveUpstream({ environment: 'production', publicApiBaseUrl: '' })
    ).toBeNull();
  });

  it('treats an empty string the same as unset', () => {
    expect(resolveUpstream({ apiBaseUrl: '   ', environment: 'local' })?.source).toBe(
      'development_default'
    );
  });
});

describe('buildUpstreamUrl', () => {
  const upstream = { base: 'https://api.example.com', source: 'api_base_url' as const };

  it('drops /api and keeps /v1', () => {
    expect(buildUpstreamUrl(upstream, '/api/v1/meta')).toBe('https://api.example.com/v1/meta');
  });

  it('preserves the query string, which carries search and pagination', () => {
    expect(buildUpstreamUrl(upstream, '/api/v1/notes', '?limit=20&offset=40')).toBe(
      'https://api.example.com/v1/notes?limit=20&offset=40'
    );
  });

  it('does not duplicate /v1', () => {
    // The bug this guards: pasting the spec's `.../api/v1` base form.
    expect(buildUpstreamUrl(upstream, '/api/v1/v1/meta')).toBe(
      'https://api.example.com/v1/v1/meta'
    );
  });

  it('collapses an upstream base that already ends in /api/v1', () => {
    const spec = { base: 'https://api.example.com/api/v1', source: 'api_base_url' as const };
    expect(buildUpstreamUrl(spec, '/api/v1/meta')).toBe('https://api.example.com/v1/meta');
  });

  it('collapses an upstream base that ends in /api', () => {
    const bare = { base: 'https://api.example.com/api', source: 'api_base_url' as const };
    expect(buildUpstreamUrl(bare, '/api/v1/meta')).toBe('https://api.example.com/v1/meta');
  });

  it('serves the route root without a trailing slash', () => {
    expect(buildUpstreamUrl(upstream, '')).toBe('https://api.example.com/v1');
  });

  it('keeps an already-upstream path usable', () => {
    expect(buildUpstreamUrl(upstream, '/v1/meta')).toBe('https://api.example.com/v1/meta');
  });
});

describe('header forwarding', () => {
  it('relays Authorization, which is the API’s only identity input', () => {
    const headers = new Headers({ authorization: 'Bearer abc', 'content-type': 'application/json' });
    const forwarded = forwardableRequestHeaders(headers);
    expect(forwarded.get('authorization')).toBe('Bearer abc');
    expect(forwarded.get('content-type')).toBe('application/json');
  });

  it('drops every hop-by-hop header', () => {
    const headers = new Headers({
      connection: 'keep-alive',
      'transfer-encoding': 'chunked',
      upgrade: 'h2c',
      'proxy-authorization': 'Basic x',
      'x-request-id': 'keep-me'
    });
    const forwarded = forwardableRequestHeaders(headers);
    for (const name of HOP_BY_HOP_HEADERS) {
      if (name === 'content-length') continue; // set by fetch, asserted below
      expect(forwarded.get(name)).toBeNull();
    }
    expect(forwarded.get('x-request-id')).toBe('keep-me');
  });

  it('does not forward a stale content-length after buffering the body', () => {
    const forwarded = forwardableRequestHeaders(new Headers({ 'content-length': '999' }));
    expect(forwarded.get('content-length')).toBeNull();
  });

  it('adds anti-buffering headers to an SSE response', () => {
    // Without these a proxy may buffer the stream, turning a token-by-token
    // answer into one late blob that reads as a hang.
    const headers = new Headers({ 'content-type': 'text/event-stream' });
    const forwarded = forwardableResponseHeaders(headers);
    expect(forwarded.get('cache-control')).toBe('no-cache, no-transform');
    expect(forwarded.get('x-accel-buffering')).toBe('no');
  });

  it('leaves a JSON response untouched apart from hop-by-hop headers', () => {
    const headers = new Headers({ 'content-type': 'application/json', connection: 'close' });
    const forwarded = forwardableResponseHeaders(headers);
    expect(forwarded.get('content-type')).toBe('application/json');
    expect(forwarded.get('connection')).toBeNull();
    expect(forwarded.get('cache-control')).toBeNull();
  });

  it('recognises the event-stream content type case-insensitively', () => {
    expect(isEventStream(new Headers({ 'content-type': 'Text/Event-Stream; charset=utf-8' }))).toBe(
      true
    );
    expect(isEventStream(new Headers({ 'content-type': 'application/json' }))).toBe(false);
    expect(isEventStream(new Headers())).toBe(false);
  });
});

describe('proxy contract', () => {
  it('relays the five methods the API actually exposes', () => {
    expect([...PROXY_METHODS]).toEqual(['GET', 'POST', 'PATCH', 'PUT', 'DELETE']);
  });

  it('uses the API’s error envelope so the frontend has one error path', () => {
    // client.ts reads error.code / error.message / error.details. A different
    // shape here would render as the generic fallback message.
    expect(proxyErrorBody('api_unreachable', 'nope')).toEqual({
      error: { code: 'api_unreachable', message: 'nope', details: {} }
    });
  });
});
