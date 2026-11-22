<script lang="ts">
  /**
   * One document in the library list.
   *
   * The row shows the *filename* as the secondary line even when the title has
   * been edited, because "the report I uploaded" is what the user recognises
   * when titles start diverging from filenames.
   */
  import type { DocumentSummary } from '$lib/api/types';
  import ProcessingStatus from './ProcessingStatus.svelte';
  import { formatBytes, relativeTime } from '$lib/utils/format';

  let {
    document,
    ondeleted,
    deletable = true
  }: {
    document: DocumentSummary;
    ondeleted?: (id: string) => void;
    deletable?: boolean;
  } = $props();
</script>

<li class="group flex items-start justify-between gap-4 px-4 py-3">
  <div class="min-w-0">
    <a
      href="/documents/{document.id}"
      class="block truncate text-sm text-ink-100 hover:text-accent-400"
    >
      {document.title}
    </a>
    <p class="mt-0.5 truncate text-[11px] text-ink-500">
      {document.filename}
      {#if document.page_count}· {document.page_count} pages{/if}
      {#if document.file_size}· {formatBytes(document.file_size)}{/if}
      · {relativeTime(document.updated_at)}
    </p>
    {#if document.status === 'failed' && !document.summary}
      <p class="mt-1 text-[11px] text-red-400/80">
        Processing failed — open the document to see why.
      </p>
    {/if}
  </div>

  <div class="flex shrink-0 items-center gap-2">
    <ProcessingStatus
      status={document.status}
      stage={document.processing_stage}
      compact
    />
    {#if deletable && ondeleted}
      <button
        class="btn-danger px-2 py-1 text-[11px] opacity-0 transition-opacity focus-visible:opacity-100 group-hover:opacity-100"
        onclick={() => ondeleted?.(document.id)}
        aria-label="Delete {document.title}"
      >
        Delete
      </button>
    {/if}
  </div>
</li>
