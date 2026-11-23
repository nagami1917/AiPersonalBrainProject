/**
 * Uploading bytes to Storage, with progress.
 *
 * ## Why this is XMLHttpRequest and not `supabase.storage.upload()`
 *
 * The spec asks for upload progress, and the Supabase JS client cannot report
 * it: `upload()` resolves once, at the end. The underlying transport is
 * `fetch`, which has no upload-progress event at all — the streaming request
 * body would have to be counted by hand, and even then it measures bytes handed
 * to the socket, not bytes the server has accepted.
 *
 * `XMLHttpRequest.upload` has had real progress events for years, so this uses
 * it. That is a deliberate step down from the nicer API for a feature the nicer
 * API cannot provide. It is confined to this file.
 *
 * ## Two callers, one implementation
 *
 * There are two ways to authorise an upload, and both are in use:
 *
 *   - **Signed URL** — the API hands back a token scoped to one object path
 *     (`POST /documents/upload-url`). No session needed; the token *is* the
 *     permission.
 *   - **Session token** — the user's own access token, constrained by the
 *     bucket's RLS policies. Used by anything that already has a live session
 *     and does not need a round-trip first.
 *
 * Unifying them here keeps one progress implementation instead of two that
 * drift. The caller says which it has; this file does not care.
 */

export type UploadProgress = {
  /** Bytes the server has acknowledged, or that have been flushed to it. */
  loaded: number;
  total: number;
  /** 0–1, or null when the size is unknown (a streamed File always knows). */
  fraction: number | null;
};

export type UploadOptions = {
  /**
   * A pre-signed upload URL. Mutually exclusive with `accessToken`.
   * The URL already carries the destination, so no path is appended.
   */
  signedUrl?: string;
  /** A Supabase access token, for the RLS-authorised path. */
  accessToken?: string;
  /** Storage origin + bucket + object path. Required with `accessToken`. */
  objectUrl?: string;
  contentType?: string;
  /** Called often — throttle in the UI, not here. */
  onProgress?: (progress: UploadProgress) => void;
  /** Standard AbortSignal so a cancel becomes a normal cancel. */
  signal?: AbortSignal;
  /** Supabase's `x-upsert` header: overwrite an existing object. */
  upsert?: boolean;
};

export class UploadError extends Error {
  constructor(
    message: string,
    readonly status: number | null = null
  ) {
    super(message);
    this.name = 'UploadError';
  }
}

/**
 * PUT a file, reporting progress.
 *
 * Resolves on 2xx. Every other outcome rejects with an `UploadError` carrying
 * the status, because "the upload failed" and "the bucket rejected this type"
 * need different messages in the UI.
 */
export function uploadWithProgress(file: File, options: UploadOptions): Promise<void> {
  const { signedUrl, accessToken, objectUrl, contentType, onProgress, signal, upsert } = options;

  const url = signedUrl ?? objectUrl;
  if (!url) {
    return Promise.reject(new UploadError('No upload destination was provided.'));
  }
  if (!signedUrl && !accessToken) {
    return Promise.reject(
      new UploadError('An upload needs either a signed URL or an access token.')
    );
  }

  return new Promise<void>((resolve, reject) => {
    if (signal?.aborted) {
      reject(new UploadError('Upload cancelled.'));
      return;
    }

    const request = new XMLHttpRequest();
    request.open('PUT', url, true);

    const type = contentType || file.type || 'application/octet-stream';
    request.setRequestHeader('Content-Type', type);
    request.setRequestHeader('x-upsert', upsert === false ? 'false' : 'true');
    if (accessToken) {
      request.setRequestHeader('Authorization', `Bearer ${accessToken}`);
    }

    request.upload.addEventListener('progress', (event) => {
      if (!onProgress) return;
      const total = event.lengthComputable ? event.total : file.size;
      onProgress({
        loaded: event.loaded,
        total,
        fraction: total > 0 ? Math.min(event.loaded / total, 1) : null
      });
    });

    // 100% of "sent to the socket" is not 100% "stored". Emit the final value on
    // load so the bar cannot stop at 97% and look stuck while the server works.
    request.addEventListener('load', () => {
      if (request.status >= 200 && request.status < 300) {
        onProgress?.({ loaded: file.size, total: file.size, fraction: 1 });
        resolve();
        return;
      }
      reject(new UploadError(describeStatus(request.status, request.responseText), request.status));
    });

    request.addEventListener('error', () => {
      reject(new UploadError('The upload could not reach storage.'));
    });
    request.addEventListener('abort', () => {
      reject(new UploadError('Upload cancelled.'));
    });
    request.addEventListener('timeout', () => {
      reject(new UploadError('The upload timed out.'));
    });

    const cancel = () => request.abort();
    signal?.addEventListener('abort', cancel, { once: true });
    // Detach on settle: a long-lived signal outliving the request would keep
    // this closure — and the whole File — alive.
    const detach = () => signal?.removeEventListener('abort', cancel);
    request.addEventListener('load', detach);
    request.addEventListener('error', detach);
    request.addEventListener('abort', detach);
    request.addEventListener('timeout', detach);

    request.send(file);
  });
}

/** Turn a Storage status code into something a writer can act on. */
function describeStatus(status: number, body: string): string {
  if (status === 400) return 'Storage rejected this file. It may exceed the size limit.';
  if (status === 401 || status === 403) {
    return 'You do not have permission to upload to this workspace.';
  }
  if (status === 404) return 'The upload destination no longer exists. Please retry.';
  if (status === 409) return 'A file already exists at that path.';
  if (status === 413) return 'This file is larger than the workspace upload limit.';
  if (status >= 500) return 'Storage had a problem storing this file. Please retry.';
  const detail = body?.trim().slice(0, 200);
  return detail ? `Upload failed: ${detail}` : `Upload failed (HTTP ${status}).`;
}
