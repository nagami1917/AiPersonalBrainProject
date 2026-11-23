<script lang="ts">
  /**
   * Drag-and-drop upload.
   *
   * Three steps, no byte proxying: `upload-url` → direct Storage PUT → confirm.
   * All three live here rather than in the page so there is exactly one upload
   * path in the app — QuickCapture's File tab renders this same component.
   *
   * Errors are reported per file and the loop continues: one rejected file
   * (too large, wrong type) must not abandon the other nine in a drop.
   */
  import { apiFetch, NexusApiError } from '$lib/api/client';
  import { uploadWithProgress, UploadError } from '$lib/api/upload';
  import { getSupabaseSync } from '$lib/api/supabase';
  import { wsPath, activeWorkspaceId } from '$lib/stores/workspace';
  import type { Meta } from '$lib/api/types';
  import { cx } from '$lib/utils/cx';
  import { formatBytes } from '$lib/utils/format';

  let {
    meta = null,
    onuploaded,
    compact = false
  }: { meta?: Meta | null; onuploaded?: () => void; compact?: boolean } = $props();

  let uploading = $state<string | null>(null);
  let dragOver = $state(false);
  let failures = $state<{ name: string; message: string }[]>([]);
  // Progress is per file and is reset for each one. Uploads run sequentially, so
  // a single pair is enough — but the *name* is kept alongside so the bar can
  // never be read as belonging to the wrong file.
  let progress = $state<{ name: string; fraction: number | null; loaded: number; total: number } | null>(
    null
  );
  let controller: AbortController | null = null;

  function cancelCurrent() {
    controller?.abort();
  }

  const accept = $derived(meta?.supported_mime_types.join(',') ?? '');

  /** The signed-in user's access token, or null when there is no session. */
  async function currentAccessToken(): Promise<string | null> {
    const { data } = await getSupabaseSync().auth.getSession();
    return data.session?.access_token ?? null;
  }


  async function handleFiles(files: FileList | null) {
    if (!files?.length) return;
    // `wsPath()` throws while no workspace is active, and that throw used to
    // surface as "No workspace is selected." next to the name of the file the
    // user had just dropped. The layout now withholds pages in that state, so
    // this is a second line of defence rather than the primary one: an upload
    // drop zone that silently accepts a file it cannot possibly send is worse
    // than one that refuses it immediately.
    if (!$activeWorkspaceId) {
      failures = [
        {
          name: files[0].name,
          message: 'No workspace is open yet. Wait for the page to finish loading, then try again.'
        }
      ];
      return;
    }
    failures = [];

    for (const file of Array.from(files)) {
      uploading = file.name;
      progress = { name: file.name, fraction: 0, loaded: 0, total: file.size };
      controller = new AbortController();
      try {
        const { document_id, upload_url } = await apiFetch<{
          document_id: string;
          file_path: string;
          upload_url: string;
          expires_at: string;
        }>(wsPath('/documents/upload-url'), {
          method: 'POST',
          body: {
            filename: file.name,
            mime_type: file.type || 'text/plain',
            file_size: file.size
          }
        });

        // Bytes go straight to Storage — never through the API — and with the
        // browser's real progress events, which the Supabase client cannot give.
        //
        // `upload_url` is the object endpoint for the path the server just
        // authorised; the storage policies validate the workspace prefix against
        // the caller's own token, so the token has to travel with the PUT. That
        // is why both are passed: the URL says *where*, the token says *who*.
        await uploadWithProgress(file, {
          signedUrl: upload_url,
          accessToken: (await currentAccessToken()) ?? undefined,
          contentType: file.type || 'application/pdf',
          signal: controller.signal,
          onProgress: (p) => {
            progress = { name: file.name, fraction: p.fraction, loaded: p.loaded, total: p.total };
          }
        });

        await apiFetch(wsPath('/documents'), {
          method: 'POST',
          body: {
            document_id,
            title: file.name.replace(/\.[^.]+$/, ''),
            filename: file.name,
            collection_id: null
          }
        });
        onuploaded?.();
      } catch (err) {
        const message =
          err instanceof NexusApiError || err instanceof UploadError
            ? err.message
            : 'Could not upload this file.';
        // A cancel is not a failure; saying so would train the user to ignore
        // the error list.
        if (!/cancelled/i.test(message)) {
          failures = [...failures, { name: file.name, message }];
        }
      } finally {
        uploading = null;
        progress = null;
        controller = null;
      }
    }
  }
</script>

<div class="space-y-2">
  <label
    class={cx(
      'flex cursor-pointer flex-col items-center justify-center rounded-xl border-2 border-dashed text-center transition-colors',
      compact ? 'px-4 py-6' : 'px-6 py-10',
      dragOver ? 'border-accent-600 bg-accent-500/5' : 'border-ink-700'
    )}
    ondragover={(e) => {
      e.preventDefault();
      dragOver = true;
    }}
    ondragleave={() => (dragOver = false)}
    ondrop={(e) => {
      e.preventDefault();
      dragOver = false;
      void handleFiles(e.dataTransfer?.files ?? null);
    }}
  >
    <input
      type="file"
      class="sr-only"
      multiple
      {accept}
      onchange={(e) => void handleFiles((e.currentTarget as HTMLInputElement).files)}
    />
    <span class="text-sm text-ink-200">
      {#if uploading}
        Uploading {uploading}…
      {:else}
        Drop files here, or click to choose
      {/if}
    </span>

    {#if uploading && progress}
      <!-- Determinate, not a spinner: a 20 MB PDF on a slow link takes long
           enough that "something is happening" is not reassurance. -->
      <div class="mt-2 w-full max-w-xs">
        <div class="h-1.5 w-full overflow-hidden rounded-full bg-ink-800">
          <div
            class="h-full rounded-full bg-accent-500 transition-[width] duration-150"
            style="width: {Math.round((progress.fraction ?? 0) * 100)}%"
            role="progressbar"
            aria-valuemin="0"
            aria-valuemax="100"
            aria-valuenow={Math.round((progress.fraction ?? 0) * 100)}
            aria-label="Upload progress for {progress.name}"
          ></div>
        </div>
        <div class="mt-1 flex items-center justify-between gap-2 text-[10px] text-ink-500">
          <span>
            {#if progress.fraction === null}
              {formatBytes(progress.loaded)} sent
            {:else}
              {Math.round(progress.fraction * 100)}% · {formatBytes(progress.loaded)} of {formatBytes(
                progress.total
              )}
            {/if}
          </span>
          <button
            type="button"
            class="text-ink-400 underline-offset-2 hover:text-ink-200 hover:underline"
            onclick={cancelCurrent}
          >
            Cancel
          </button>
        </div>
      </div>
    {/if}
    <span class="mt-1 text-[11px] text-ink-500">
      {#if meta}
        {meta.supported_mime_types.length} formats · up to {formatBytes(meta.max_upload_bytes)}
      {:else}
        PDF, DOCX, TXT and Markdown
      {/if}
    </span>
    <span class="mt-0.5 text-[10px] text-ink-600">
      Files go straight to private storage, then get processed on the backend.
    </span>
  </label>

  {#if failures.length > 0}
    <ul class="space-y-1">
      {#each failures as failure (failure.name)}
        <li
          class="rounded border border-red-900/60 bg-red-950/30 px-2 py-1 text-[11px] text-red-300"
        >
          {failure.name}: {failure.message}
        </li>
      {/each}
    </ul>
  {/if}
</div>
