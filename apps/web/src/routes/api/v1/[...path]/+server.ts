/**
 * Same-origin API proxy: `/api/v1/*` → `{API_BASE_URL}/v1/*`.
 *
 * This is the production counterpart of the old dev-only Vite proxy, and it
 * exists because that proxy had no equivalent in the Vercel build. Same-origin
 * is the *default* deployment shape (no CORS, nothing reachable at localhost
 * from a user's browser), so "works in dev, 404s in production" was a blocking
 * defect rather than a documentation gap.
 *
 * ## What it is not
 *
 * Not a security boundary. It relays `Authorization` unchanged and FastAPI
 * verifies the JWT exactly as before. The proxy never inspects or caches
 * tokens, never talks to Postgres, and holds no Supabase service-role key — so
 * it cannot become a way around RLS (rules 1 and 8).
 *
 * ## Streaming
 *
 * The chat answer is SSE. The body is relayed as a `ReadableStream` rather than
 * buffered, because buffering turns a token-by-token answer into one late blob
 * that reads as a hang.
 */

import { json, type RequestEvent } from '@sveltejs/kit';
import { env as privateEnv } from '$env/dynamic/private';
import { env as publicEnv } from '$env/dynamic/public';

import {
  PROXY_METHODS,
  buildUpstreamUrl,
  forwardableRequestHeaders,
  forwardableResponseHeaders,
  proxyErrorBody,
  resolveUpstream
} from '$lib/server/api-proxy';

/** Long enough for a streaming answer, short enough to fail honestly. */
export const config = { maxDuration: 60 };

/** Methods with no body; reading one would block until the client hangs up. */
const BODYLESS = new Set(['GET', 'HEAD']);

/**
 * Every method funnels through here. SvelteKit routes per method rather than
 * exposing one `handle`, so the five exports below are one line each — the
 * behaviour lives in `relay`, which keeps the method list and the logic from
 * disagreeing.
 */
export async function GET(event: RequestEvent) {
  return relay(event);
}

export async function POST(event: RequestEvent) {
  return relay(event);
}

export async function PATCH(event: RequestEvent) {
  return relay(event);
}

export async function PUT(event: RequestEvent) {
  return relay(event);
}

export async function DELETE(event: RequestEvent) {
  return relay(event);
}

async function relay({ request, url, fetch }: RequestEvent) {
  const method = request.method.toUpperCase();
  const relayed = (PROXY_METHODS as readonly string[]).includes(method);
  if (!relayed) {
    return json(proxyErrorBody('method_not_allowed', `The API proxy does not relay ${method}.`), {
      status: 405,
      headers: { allow: PROXY_METHODS.join(', ') }
    });
  }

  const upstream = resolveUpstream({
    apiBaseUrl: privateEnv.API_BASE_URL,
    publicApiBaseUrl: publicEnv.PUBLIC_API_BASE_URL,
    environment: privateEnv.ENVIRONMENT
  });

  if (!upstream) {
    // Production with no target configured. Saying so beats forwarding to
    // localhost, which fails as ECONNREFUSED with nothing an operator can act
    // on — and never silently reaching 127.0.0.1 from a deployed server.
    const message =
      'The API proxy has no upstream: set API_BASE_URL (server-only) to the FastAPI ' +
      'origin, or set PUBLIC_API_BASE_URL to an absolute URL so the browser calls ' +
      'the API directly. See docs/DEPLOYMENT.md §3.';
    console.error(`[api-proxy] ${message}`);
    return json(proxyErrorBody('api_upstream_not_configured', message), { status: 503 });
  }

  // The *actual* pathname, not one rebuilt from `params`: for `/api/v1` (the
  // route root) SvelteKit hands over an empty rest parameter, and rebuilding
  // would lose the `/api/v1` part that the prefix translation needs.
  const target = buildUpstreamUrl(upstream, url.pathname, url.search);

  try {
    return await forward({ request, method, target, fetch });
  } catch (error) {
    // A network-level failure (DNS, refused, TLS). Reported as a gateway error
    // so the frontend shows a cause instead of a generic 500.
    const detail = error instanceof Error ? error.message : String(error);
    console.error(`[api-proxy] ${method} ${target} failed: ${detail}`);
    return json(
      proxyErrorBody(
        'api_unreachable',
        'The API could not be reached. It may be starting up or temporarily down.',
        { upstream_source: upstream.source }
      ),
      { status: 502 }
    );
  }
}

type ForwardArgs = {
  request: Request;
  method: string;
  target: string;
  fetch: typeof globalThis.fetch;
};

async function forward({ request, method, target, fetch }: ForwardArgs) {
  // `request.body` rather than `content-length`: a chunked upload has no
  // content-length, and checking it there would silently drop the body.
  const hasBody = !BODYLESS.has(method) && request.body !== null;

  const upstreamResponse = await fetch(target, {
    method,
    headers: forwardableRequestHeaders(request.headers),
    // `signal` links the two lifetimes: a user closing the tab aborts the
    // upstream request instead of leaving FastAPI generating into a void.
    signal: request.signal,
    redirect: 'manual',
    body: hasBody ? request.body : undefined,
    // Required by undici when `body` is a stream.
    ...(hasBody ? { duplex: 'half' } : {})
  });

  const headers = forwardableResponseHeaders(upstreamResponse.headers);

  if (upstreamResponse.status === 304 || upstreamResponse.status === 204) {
    // No body permitted, and passing one would violate the status's contract.
    return new Response(null, { status: upstreamResponse.status, headers });
  }

  return new Response(upstreamResponse.body, {
    status: upstreamResponse.status,
    statusText: upstreamResponse.statusText,
    headers
  });
}
