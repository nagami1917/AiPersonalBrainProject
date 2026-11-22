/**
 * Typed API client.
 *
 * Every call goes to FastAPI with the Supabase access token. The browser never
 * talks to Postgres, and never sees a response that RLS would have filtered —
 * the API's own RLS-bound connection enforces that server-side.
 *
 * Errors are normalized into NexusApiError so there is exactly one error path
 * in the UI, matching the backend's single error envelope.
 */
import { env } from '$lib/utils/env';
import { getSupabase } from '$lib/api/supabase';

export class NexusApiError extends Error {
  code: string;
  status: number;
  details: Record<string, unknown>;

  constructor(status: number, code: string, message: string, details: Record<string, unknown> = {}) {
    super(message);
    this.name = 'NexusApiError';
    this.status = status;
    this.code = code;
    this.details = details;
  }
}

async function accessToken(): Promise<string | null> {
  const supabase = await getSupabase();
  const { data } = await supabase.auth.getSession();
  return data.session?.access_token ?? null;
}

type RequestOptions = {
  method?: 'GET' | 'POST' | 'PATCH' | 'DELETE';
  body?: unknown;
  signal?: AbortSignal;
};

export async function apiFetch<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const token = await accessToken();
  const headers: Record<string, string> = { 'Content-Type': 'application/json' };
  if (token) headers.Authorization = `Bearer ${token}`;

  const response = await fetch(`${env.apiBaseUrl}${path}`, {
    method: options.method ?? 'GET',
    headers,
    body: options.body === undefined ? undefined : JSON.stringify(options.body),
    signal: options.signal
  });

  if (response.status === 204) {
    return undefined as T;
  }

  const payload = await response.json().catch(() => null);

  if (!response.ok) {
    const error = payload?.error ?? {};
    throw new NexusApiError(
      response.status,
      error.code ?? 'unknown_error',
      error.message ?? 'Something went wrong. Please try again.',
      error.details ?? {}
    );
  }

  return payload as T;
}

/** Streaming chat: SSE parsed into typed events, with sources arriving first. */
export type ChatEvent =
  | { type: 'sources'; sources: SourcePreview[] }
  | { type: 'delta'; text: string }
  | { type: 'citations'; citations: Citation[]; grounded: boolean }
  | { type: 'done'; message_id: string; conversation_id: string; latency_ms: number }
  | { type: 'error'; code: string; message: string };

/** Every source names the table its id belongs to, so links can be correct. */
export type SourcePreview = {
  marker: number;
  source_kind: 'note' | 'document';
  source_id: string;
  source_title: string;
  filename: string | null;
  page: number | null;
  heading_path: string | null;
};

export type Citation = {
  marker: number;
  chunk_id: string;
  source_kind: 'note' | 'document';
  source_id: string;
  source_title: string;
  /** Uploaded file name for document sources; the title for notes. */
  filename: string | null;
  quote: string;
  page: number | null;
  heading_path: string | null;
  score: number;
};

export async function streamChat(
  workspaceId: string,
  body: Record<string, unknown>,
  onEvent: (event: ChatEvent) => void,
  signal?: AbortSignal
): Promise<void> {
  const token = await accessToken();
  const response = await fetch(`${env.apiBaseUrl}/v1/workspaces/${workspaceId}/chat/stream`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {})
    },
    body: JSON.stringify(body),
    signal
  });

  if (!response.ok || !response.body) {
    const payload = await response.json().catch(() => null);
    onEvent({
      type: 'error',
      code: payload?.error?.code ?? 'stream_failed',
      message: payload?.error?.message ?? 'Could not start the answer.'
    });
    return;
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    // SSE frames are separated by a blank line.
    let boundary = buffer.indexOf('\n\n');
    while (boundary !== -1) {
      const frame = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary + 2);
      boundary = buffer.indexOf('\n\n');

      const eventLine = frame.split('\n').find((l) => l.startsWith('event: '));
      const dataLine = frame.split('\n').find((l) => l.startsWith('data: '));
      if (!eventLine || !dataLine) continue;

      const type = eventLine.slice(7).trim();
      let data: Record<string, unknown>;
      try {
        data = JSON.parse(dataLine.slice(6));
      } catch {
        continue;
      }

      onEvent({ type, ...data } as ChatEvent);
    }
  }
}
