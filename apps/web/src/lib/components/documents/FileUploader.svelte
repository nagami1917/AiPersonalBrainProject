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
  import { uploadFile } from '$lib/api/supabase';
  import { wsPath } from '$lib/stores/workspace';
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

  const accept = $derived(meta?.supported_mime_types.join(',') ?? '');

  async function handleFiles(files: FileList | null) {
    if (!files?.length) return;
    failures = [];

    for (const file of Array.from(files)) {
      uploading = file.name;
      try {
        const { document_id, file_path } = await apiFetch<{
          document_id: string;
          file_path: string;
        }>(wsPath('/documents/upload-url'), {
          method: 'POST',
          body: {
            filename: file.name,
            mime_type: file.type || 'text/plain',
            file_size: file.size
          }
        });

        await uploadFile(file_path, file);

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
        failures = [
          ...failures,
          {
            name: file.name,
            message:
              err instanceof NexusApiError ? err.message : 'Could not upload this file.'
          }
        ];
      } finally {
        uploading = null;
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
