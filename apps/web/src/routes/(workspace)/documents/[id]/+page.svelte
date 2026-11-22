<script lang="ts">
  import { onMount } from 'svelte';
  import { page } from '$app/state';
  import { apiFetch, NexusApiError } from '$lib/api/client';
  import type { DocumentDetail } from '$lib/api/types';
  import StatusPill from '$lib/components/StatusPill.svelte';
  import ErrorNotice from '$lib/components/ErrorNotice.svelte';
  import { formatBytes, relativeTime } from '$lib/utils/format';

  let document = $state<DocumentDetail | null>(null);
  let error = $state<{ message: string; code?: string } | null>(null);
  let loading = $state(true);
  let polling: ReturnType<typeof setInterval> | null = null;

  const id = $derived(page.params.id);

  async function fetchDocument() {
    try {
      document = await apiFetch<DocumentDetail>(`/v1/documents/${id}`);
      // Poll only while there is genuinely something to wait for.
      if (document.status === 'processing' || document.status === 'pending') {
        polling ??= setInterval(fetchDocument, 2500);
      } else if (polling) {
        clearInterval(polling);
        polling = null;
      }
    } catch (err) {
      error =
        err instanceof NexusApiError
          ? { message: err.message, code: err.code }
          : { message: 'Could not load this document.' };
      if (polling) clearInterval(polling);
    } finally {
      loading = false;
    }
  }

  onMount(() => {
    void fetchDocument();
    return () => {
      if (polling) clearInterval(polling);
    };
  });

  async function reprocess() {
    await apiFetch(`/v1/documents/${id}/reprocess`, { method: 'POST' });
    void fetchDocument();
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
    <header class="space-y-2">
      <div class="flex flex-wrap items-center gap-3">
        <h1 class="text-xl font-semibold tracking-tight text-ink-50">{document.title}</h1>
        <StatusPill status={document.status} stage={document.processing_stage} />
      </div>

      <p class="text-xs text-ink-500">
        {document.source_type === 'note' ? 'Note' : (document.mime_type ?? 'File')}
        {#if document.page_count}· {document.page_count} pages{/if}
        {#if document.file_size}· {formatBytes(document.file_size)}{/if}
        · {document.chunk_count} searchable passages
        · updated {relativeTime(document.updated_at)}
      </p>

      {#if document.error_message}
        <div class="rounded-lg border border-red-900/60 bg-red-950/30 px-3 py-2 text-xs text-red-300">
          {document.error_message}
          <button class="ml-2 underline hover:no-underline" onclick={reprocess}>Retry processing</button>
        </div>
      {/if}
    </header>

    {#if document.summary}
      <section class="card p-4">
        <h2 class="mb-1 text-xs font-medium uppercase tracking-wide text-ink-500">Summary</h2>
        <p class="text-sm leading-relaxed text-ink-200">{document.summary}</p>
      </section>
    {/if}

    {#if document.status === 'processing'}
      <p class="text-xs text-ink-400">
        Processing — this page updates automatically. Retrieval will not use this document until it
        is ready.
      </p>
    {/if}

    <section class="space-y-2">
      <h2 class="text-xs font-medium uppercase tracking-wide text-ink-500">Extracted text</h2>
      {#if document.raw_text}
        <div class="max-h-[28rem] overflow-y-auto rounded-xl border border-ink-800 bg-ink-900/40 p-4">
          <pre class="whitespace-pre-wrap font-sans text-[13px] leading-relaxed text-ink-300">
{document.raw_text}</pre>
        </div>
      {:else}
        <p class="rounded-lg border border-ink-800 px-4 py-6 text-center text-xs text-ink-500">
          Text has not been extracted yet.
        </p>
      {/if}
    </section>
  {/if}
</div>
