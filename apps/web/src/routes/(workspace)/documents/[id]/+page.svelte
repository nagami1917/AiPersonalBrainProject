<script lang="ts">
  /**
   * Document detail: header, live processing state, extracted text.
   *
   * Polling is driven by `ProcessingStatus`' inputs being stale, not by a timer
   * for its own sake: the interval is created when the document is mid-pipeline
   * and cleared the moment it is not. A page that polls forever is a page that
   * keeps a laptop awake for no reason.
   */
  import { onMount } from 'svelte';
  import { page } from '$app/state';
  import { apiFetch, NexusApiError } from '$lib/api/client';
  import { wsPath, activeWorkspaceId } from '$lib/stores/workspace';
  import type { DocumentDetail } from '$lib/api/types';
  import ErrorNotice from '$lib/components/ErrorNotice.svelte';
  import ProcessingStatus from '$lib/components/documents/ProcessingStatus.svelte';
  import DocumentPreview from '$lib/components/documents/DocumentPreview.svelte';
  import { formatBytes, relativeTime } from '$lib/utils/format';

  let document = $state<DocumentDetail | null>(null);
  let error = $state<{ message: string; code?: string } | null>(null);
  let loading = $state(true);
  let polling: ReturnType<typeof setInterval> | null = null;
  let retrying = $state(false);

  const id = $derived(page.params.id);

  function stopPolling() {
    if (polling) {
      clearInterval(polling);
      polling = null;
    }
  }

  async function fetchDocument() {
    try {
      if (!$activeWorkspaceId) return;
      document = await apiFetch<DocumentDetail>(wsPath(`/documents/${id}`));
      // Poll only while there is genuinely something to wait for.
      if (document.status === 'processing' || document.status === 'pending') {
        polling ??= setInterval(fetchDocument, 2500);
      } else {
        stopPolling();
      }
    } catch (err) {
      error =
        err instanceof NexusApiError
          ? { message: err.message, code: err.code }
          : { message: 'Could not load this document.' };
      stopPolling();
    } finally {
      loading = false;
    }
  }

  onMount(() => {
    void fetchDocument();
    return stopPolling;
  });

  async function reprocess() {
    retrying = true;
    try {
      await apiFetch(wsPath(`/documents/${id}/reprocess`), { method: 'POST' });
      await fetchDocument();
    } catch (err) {
      error =
        err instanceof NexusApiError
          ? { message: err.message, code: err.code }
          : { message: 'Could not queue that for reprocessing.' };
    } finally {
      retrying = false;
    }
  }
</script>

<svelte:head><title>{document?.title ?? 'Document'} · NEXUS</title></svelte:head>

<div class="space-y-6">
  <a href="/documents" class="text-xs text-ink-500 hover:text-accent-400">← Documents</a>

  {#if error}
    <ErrorNotice message={error.message} code={error.code} />
  {:else if loading}
    <div class="skeleton h-8 w-64"></div>
  {:else if document}
    <header class="space-y-3">
      <div class="flex flex-wrap items-center gap-3">
        <h1 class="text-xl font-semibold tracking-tight text-ink-50">{document.title}</h1>
      </div>

      <p class="text-xs text-ink-500">
        {document.filename}
        {#if document.page_count}· {document.page_count} pages{/if}
        {#if document.file_size}· {formatBytes(document.file_size)}{/if}
        · {document.chunk_count} searchable passages
        · updated {relativeTime(document.updated_at)}
      </p>

      <ProcessingStatus
        status={document.status}
        stage={document.processing_stage}
        errorMessage={document.error_message}
      />

      {#if document.status === 'failed'}
        <button class="btn-ghost px-2 py-1 text-xs" onclick={reprocess} disabled={retrying}>
          {retrying ? 'Queueing…' : 'Retry processing'}
        </button>
      {/if}
    </header>

    {#if document.summary}
      <section class="card p-4">
        <h2 class="mb-1 text-xs font-medium uppercase tracking-wide text-ink-500">Summary</h2>
        <p class="text-sm leading-relaxed text-ink-200">{document.summary}</p>
      </section>
    {/if}

    <DocumentPreview {document} />
  {/if}
</div>
