<script lang="ts">
  /**
   * Document library: upload, list, delete.
   *
   * Composition only — the upload handshake lives in FileUploader and the row
   * in DocumentCard, so QuickCapture can reuse the uploader without inheriting
   * this page's list.
   */
  import { onMount } from 'svelte';
  import { apiFetch, NexusApiError } from '$lib/api/client';
  import { wsPath, activeWorkspaceId } from '$lib/stores/workspace';
  import type { DocumentSummary, Meta } from '$lib/api/types';
  import ErrorNotice from '$lib/components/ErrorNotice.svelte';
  import EmptyState from '$lib/components/EmptyState.svelte';
  import FileUploader from '$lib/components/documents/FileUploader.svelte';
  import DocumentCard from '$lib/components/documents/DocumentCard.svelte';
  import { formatBytes } from '$lib/utils/format';

  let documents = $state<DocumentSummary[]>([]);
  let meta = $state<Meta | null>(null);
  let loading = $state(true);
  let error = $state<{ message: string; code?: string } | null>(null);

  async function load() {
    if (!$activeWorkspaceId) return;
    try {
      const [docs, metaResponse] = await Promise.all([
        apiFetch<{ items: DocumentSummary[] }>(wsPath('/documents')),
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

  async function remove(id: string) {
    try {
      await apiFetch(wsPath(`/documents/${id}`), { method: 'DELETE' });
      documents = documents.filter((doc) => doc.id !== id);
    } catch (err) {
      error =
        err instanceof NexusApiError
          ? { message: err.message, code: err.code }
          : { message: 'Could not delete that document.' };
    }
  }
</script>

<svelte:head><title>Documents · NEXUS</title></svelte:head>

<div class="space-y-6">
  <header class="flex flex-wrap items-end justify-between gap-3">
    <div>
      <h1 class="text-xl font-semibold tracking-tight text-ink-50">Documents</h1>
      <p class="mt-1 text-sm text-ink-400">
        PDF, DOCX, TXT and Markdown. Processing happens in the background; the status names the
        exact step it is on.
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

  <FileUploader {meta} onuploaded={() => void load()} />

  {#if loading}
    <div class="space-y-2">
      {#each [1, 2, 3] as i (i)}<div class="skeleton h-16 w-full"></div>{/each}
    </div>
  {:else if documents.length === 0}
    <EmptyState
      title="No documents yet"
      description="Add a PDF or a note and it becomes searchable, citable knowledge."
    />
  {:else}
    <ul class="divide-y divide-ink-800 rounded-xl border border-ink-800">
      {#each documents as document (document.id)}
        <DocumentCard {document} ondeleted={remove} />
      {/each}
    </ul>
  {/if}
</div>
