import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { UploadError, uploadWithProgress } from './upload';

/**
 * Upload progress, tested against a stand-in XMLHttpRequest.
 *
 * Node has no XHR, so the double below implements the parts the uploader
 * touches: `open`, `setRequestHeader`, `upload` events, the request lifecycle
 * events, and `send`. It does not stub the uploader itself — the code under
 * test runs unmodified, which is the point. A test that mocked
 * `uploadWithProgress` would only assert that it was called.
 *
 * The behaviours worth pinning down are the ones that produce a wrong UI:
 *
 *   - progress must be a fraction of the *file*, not of a chunk;
 *   - a 200 must report 100%, because "sent" and "stored" differ;
 *   - a non-2xx must reject with the status, so the message can be specific;
 *   - the auth header must appear only when a token was supplied — sending an
 *     empty `Bearer ` to a signed URL is a different (and confusing) failure.
 */

type Listener = (event: unknown) => void;

class FakeXhr {
  static instances: FakeXhr[] = [];

  method = '';
  url = '';
  headers: Record<string, string> = {};
  sentBody: unknown = null;
  aborted = false;

  private listeners: Record<string, Listener[]> = {};
  upload = { addEventListener: (type: string, fn: Listener) => this.#add(`upload:${type}`, fn) };

  constructor() {
    FakeXhr.instances.push(this);
  }

  #add(key: string, fn: Listener) {
    (this.listeners[key] ??= []).push(fn);
  }

  addEventListener(type: string, fn: Listener) {
    this.#add(type, fn);
  }

  removeEventListener(type: string, fn: Listener) {
    this.listeners[type] = (this.listeners[type] ?? []).filter((f) => f !== fn);
  }

  open(method: string, url: string) {
    this.method = method;
    this.url = url;
  }

  setRequestHeader(name: string, value: string) {
    this.headers[name] = value;
  }

  send(body: unknown) {
    this.sentBody = body;
  }

  abort() {
    this.aborted = true;
    this.fire('abort', {});
  }

  // --- test controls ---
  fire(type: string, event: unknown) {
    for (const fn of this.listeners[type] ?? []) fn(event);
  }

  fireUploadProgress(loaded: number, total: number) {
    this.fire('upload:progress', { lengthComputable: true, loaded, total });
  }

  respond(status: number, body = '') {
    this.status = status;
    this.responseText = body;
    this.fire('load', {});
  }

  status = 0;
  responseText = '';

  static latest(): FakeXhr {
    const instance = FakeXhr.instances.at(-1);
    if (!instance) throw new Error('no XMLHttpRequest was created');
    return instance;
  }
}

function makeFile(size = 1000, name = 'notes.pdf'): File {
  // A real File would need a size, and the uploader reads `file.size` for the
  // final progress event and for the unknown-length fallback.
  return { name, size, type: 'application/pdf' } as File;
}

describe('uploadWithProgress', () => {
  beforeEach(() => {
    FakeXhr.instances = [];
    vi.stubGlobal('XMLHttpRequest', FakeXhr);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('PUTs the file to the signed URL', async () => {
    const promise = uploadWithProgress(makeFile(), {
      signedUrl: 'https://storage.test/object/upload/sign/abc',
      contentType: 'application/pdf'
    });
    const xhr = FakeXhr.latest();
    expect(xhr.method).toBe('PUT');
    expect(xhr.url).toBe('https://storage.test/object/upload/sign/abc');
    expect(xhr.headers['Content-Type']).toBe('application/pdf');

    xhr.respond(200);
    await expect(promise).resolves.toBeUndefined();
  });

  it('reports progress as a fraction of the file', async () => {
    const seen: { loaded: number; total: number; fraction: number | null }[] = [];
    const promise = uploadWithProgress(makeFile(1000), {
      signedUrl: 'https://storage.test/x',
      onProgress: (p) => seen.push(p)
    });

    const xhr = FakeXhr.latest();
    xhr.fireUploadProgress(250, 1000);
    xhr.fireUploadProgress(750, 1000);
    expect(seen).toHaveLength(2);
    expect(seen[0]).toMatchObject({ loaded: 250, total: 1000, fraction: 0.25 });
    expect(seen[1]).toMatchObject({ loaded: 750, total: 1000, fraction: 0.75 });

    xhr.respond(200);
    await promise;
  });

  it('ends at exactly 100% on success, even if no progress arrived', async () => {
    // A small file can finish before the first progress event fires; without
    // this the bar would sit at 0% and then vanish.
    const seen: number[] = [];
    const promise = uploadWithProgress(makeFile(500), {
      signedUrl: 'https://storage.test/x',
      onProgress: (p) => seen.push(p.fraction ?? -1)
    });
    FakeXhr.latest().respond(200);
    await promise;
    expect(seen).toEqual([1]);
  });

  it('does not exceed 100% when the browser over-reports', async () => {
    const seen: (number | null)[] = [];
    const promise = uploadWithProgress(makeFile(100), {
      signedUrl: 'https://storage.test/x',
      onProgress: (p) => seen.push(p.fraction)
    });
    // Some proxies report `loaded` beyond `total`.
    FakeXhr.latest().fireUploadProgress(150, 100);
    expect(Math.max(...(seen as number[]))).toBeLessThanOrEqual(1);
    FakeXhr.latest().respond(200);
    await promise;
  });

  it('sends the bearer token only when one was supplied', async () => {
    const withToken = uploadWithProgress(makeFile(), {
      accessToken: 'jwt-token',
      objectUrl: 'https://proj.supabase.co/storage/v1/object/nexus-documents/ws/doc/file.pdf'
    });
    expect(FakeXhr.latest().headers['Authorization']).toBe('Bearer jwt-token');
    FakeXhr.latest().respond(200);
    await withToken;

    const signed = uploadWithProgress(makeFile(), { signedUrl: 'https://storage.test/signed' });
    // An empty `Bearer ` header on a signed upload is a distinct, confusing 401.
    expect(FakeXhr.latest().headers['Authorization']).toBeUndefined();
    FakeXhr.latest().respond(200);
    await signed;
  });

  it('rejects with the status so the message can be specific', async () => {
    const promise = uploadWithProgress(makeFile(), { signedUrl: 'https://storage.test/x' });
    FakeXhr.latest().respond(413, 'Payload too large');

    await expect(promise).rejects.toBeInstanceOf(UploadError);
    await expect(promise).rejects.toMatchObject({ status: 413 });
    await expect(promise).rejects.toThrow(/larger than the workspace upload limit/);
  });

  it('explains a permission failure in user terms', async () => {
    const promise = uploadWithProgress(makeFile(), { signedUrl: 'https://storage.test/x' });
    FakeXhr.latest().respond(403);
    await expect(promise).rejects.toThrow(/do not have permission/);
  });

  it('distinguishes "unreachable" from "rejected"', async () => {
    const promise = uploadWithProgress(makeFile(), { signedUrl: 'https://storage.test/x' });
    // Network failure: no status at all.
    FakeXhr.latest().fire('error', {});
    await expect(promise).rejects.toMatchObject({ status: null });
    await expect(promise).rejects.toThrow(/could not reach storage/);
  });

  it('cancels an in-flight upload through an AbortSignal', async () => {
    const controller = new AbortController();
    const promise = uploadWithProgress(makeFile(), {
      signedUrl: 'https://storage.test/x',
      signal: controller.signal
    });
    const xhr = FakeXhr.latest();
    controller.abort();

    expect(xhr.aborted).toBe(true);
    await expect(promise).rejects.toThrow(/cancelled/);
  });

  it('refuses before opening a request when already aborted', async () => {
    const controller = new AbortController();
    controller.abort();
    const before = FakeXhr.instances.length;

    await expect(
      uploadWithProgress(makeFile(), {
        signedUrl: 'https://storage.test/x',
        signal: controller.signal
      })
    ).rejects.toThrow(/cancelled/);
    expect(FakeXhr.instances.length).toBe(before);
  });

  it('requires a destination and an authorisation mode', async () => {
    await expect(uploadWithProgress(makeFile(), {})).rejects.toThrow(/destination/);
    await expect(
      uploadWithProgress(makeFile(), { objectUrl: 'https://storage.test/x' })
    ).rejects.toThrow(/signed URL or an access token/);
  });

  it('sets x-upsert so a retry overwrites its own partial object', async () => {
    const promise = uploadWithProgress(makeFile(), { signedUrl: 'https://storage.test/x' });
    expect(FakeXhr.latest().headers['x-upsert']).toBe('true');
    FakeXhr.latest().respond(200);
    await promise;
  });
});
