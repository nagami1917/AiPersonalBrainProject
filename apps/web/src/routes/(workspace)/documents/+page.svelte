<script lang="ts">
  import { onMount } from 'svelte';
  import { apiFetch, NexusApiError } from '$lib/api/client';
  import { uploadFile } from '$lib/api/supabase';
  import type { DocumentSummary, Meta } from '$lib/api/types';
  import StatusPill from '$lib/components/StatusPill.svelte';
  import ErrorNotice from '$lib/components/ErrorNotice.svelte';
  import EmptyState from '$lib/components/EmptyState.svelte';
  import { formatBytes, relativeTime } from '$lib/utils/format';
  import { cx } from '$lib/utils/cx';

  let documents = $state<DocumentSummary[]>([]);
  let meta = $state<Meta | null>(null);
  let loading = $state(true);
  let uploading = $state<string | null>(null);
  let error = $state<{ message: string; code?: string } | null>(null);
  let dragOver = $state(false);

  async function load() {
    try {
      const [docs, metaResponse] = await Promise.all([
        apiFetch<{ items: DocumentSummary[] }>('/v1/documents'),
        apiFetch<Meta>('/v1/meta')
      ]);
      documents = docs.items;
      meta = metaResponse;
    } catch (err) {
      error =
        err instanceof NexusApiError
          ? { message: err.message, code: err.code }
          : { message: 'Could not load documents.' };
    } finally {
      loading = false;
    }
  }

  onMount(load);

  /** upload-url → direct Storage PUT → confirm. Three steps, no byte proxying. */
  async function handleFiles(files: FileList | null) {
    if (!files?.length) return;
    error = null;

    for (const file of Array.from(files)) {
      uploading = file.name;
      try {
        const { document_id, storage_path } = await apiFetch<{
          document_id: string;
          storage_path: string;
        }>('/v1/documents/upload-url', {
          method: 'POST',
          body: { filename: file.name, mime_type: file.type || 'text/plain', file_size: file.size }
        });

        await uploadFile(storage_path, file);

        await apiFetch('/v1/documents', {
          method: 'POST',
          body: {
            document_id,
            title: file.name.replace(/\.[^.]+$/, ''),
            collection_id: null
          }
        });
        await load();
      } catch (err) {
        error =
          err instanceof NexusApiError
            ? { message: `${file.name}: ${err.message}`, code: err.code }
            : { message: `Could not upload ${file.name}.` };
      } finally {
        uploading = null;
      }
    }
  }

  async function remove(id: string) {
    try {
      await apiFetch(`/v1/documents/${id}`, { method: 'DELETE' });
      documents = documents.filter((d) => d.id !== id);
    } catch (err) {
      error = err instanceof NexusApiError ? { message: err.message, code: err.code } : null;
    }
  }

  const accept = $derived(meta?.supported_mime_types.join(',') ?? '');
</script>

<svelte:head><title>Documents · NEXUS</title></svelte:head>

<div class="space-y-6">
  <header class="flex flex-wrap items-end justify-between gap-3">
    <div>
      <h1 class="text-xl font-semibold tracking-tight text-ink-50">Documents</h1>
      <p class="mt-1 text-sm text-ink-400">
        PDF, DOCX, TXT and Markdown. Processing happens in the background; the status tells you
        exactly which step it is on.
      </p>
    </div>
    {#if meta}
      <p class="text-[11px] text-ink-500">
        Limit {formatBytes(meta.max_upload_bytes)} · embeds with {meta.embedding_model}
      </p>
    {/if}
  </header>

  {#if error}
    <ErrorNotice message={error.message} code={error.code} />
  {/if}

  <label
    class={cx(
      'flex cursor-pointer flex-col items-center justify-center rounded-xl border-2 border-dashed px-6 py-10 text-center transition-colors',
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
      handleFiles(e.dataTransfer?.files ?? null);
    }}
  >
    <input
      type="file"
      class="sr-only"
      multiple
      {accept}
      onchange={(e) => handleFiles((e.currentTarget as HTMLInputElement).files)}
    />
    <span class="text-sm text-ink-200">
      {#if uploading}
        Uploading {uploading}…
      {:else}
        Drop files here, or click to choose
      {/if}
    </span>
    <span class="mt-1 text-[11px] text-ink-500">
      Files go straight to private storage, then get processed on the backend.
    </span>
  </label>

  {#if loading}
    <div class="space-y-2">
      {#each [1, 2, 3] as i}<div class="skeleton h-16 w-full"></div>{/each}
    </div>
  {:else if documents.length === 0}
    <EmptyState
      title="No documents yet"
      description="Add a PDF or a note and it becomes searchable, citable knowledge."
    />
  {:else}
    <ul class="divide-y divide-ink-800 rounded-xl border border-ink-800">
      {#each documents as doc (doc.id)}
        <li class="group flex items-start justify-between gap-4 px-4 py-3">
          <div class="min-w-0">
            <a href="/documents/{doc.id}" class="block truncate text-sm text-ink-100 hover:text-accent-400">
              {doc.title}
            </a>
            <p class="mt-0.5 text-[11px] text-ink-500">
              {doc.source_type === 'note' ? 'Note' : (doc.mime_type ?? 'File')}
              {#if doc.file_size}· {formatBytes(doc.file_size)}{/if}
              {#if doc.page_count}· {doc.page_count} pages{/if}
              · {relativeTime(doc.updated_at)}
            </p>
            {#if doc.status === 'failed' && doc.summary === null}
              <p class="mt-1 text-[11px] text-red-400/80">
                Processing failed — open the document to see why.
              </p>
            {/if}
          </div>
          <div class="flex shrink-0 items-center gap-2">
            <StatusPill status={doc.status} stage={doc.processing_stage} />
            <button
              class="btn-danger px-2 py-1 text-[11px] opacity-0 transition-opacity group-hover:opacity-100"
              onclick={() => remove(doc.id)}
              aria-label="Delete {doc.title}"
            >
              Delete
            </button>
          </div>
        </li>
      {/each}
    </ul>
  {/if}
</div>
