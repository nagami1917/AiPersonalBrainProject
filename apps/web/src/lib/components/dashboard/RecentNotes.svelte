<script lang="ts">
  /**
   * The five most recently touched notes.
   *
   * `kind` is a prop rather than a second component: notes and documents are
   * different tables with different fields, but the panel that lists them is
   * the same panel. Two near-identical components would drift on the empty
   * state and the link target, which is exactly what a reader notices.
   */
  import type { DocumentSummary, NoteSummary } from '$lib/api/types';
  import ProcessingStatus from '$lib/components/documents/ProcessingStatus.svelte';
  import { relativeTime } from '$lib/utils/format';

  type Props = {
    title: string;
    href: string;
    loading?: boolean;
    emptyLabel?: string;
    notes?: NoteSummary[];
    documents?: DocumentSummary[];
  };

  let {
    title,
    href,
    loading = false,
    emptyLabel = 'Nothing here yet.',
    notes = [],
    documents = []
  }: Props = $props();

  const count = $derived(notes.length + documents.length);
</script>

<section class="card space-y-3 p-4">
  <div class="flex items-center justify-between">
    <h2 class="text-sm font-medium text-ink-200">{title}</h2>
    <a {href} class="text-[11px] text-accent-400 hover:underline">All</a>
  </div>

  {#if loading}
    <div class="space-y-2">
      {#each [1, 2, 3] as i (i)}<div class="skeleton h-9 w-full"></div>{/each}
    </div>
  {:else if count === 0}
    <p class="py-6 text-center text-xs text-ink-500">{emptyLabel}</p>
  {:else if notes.length > 0}
    <ul class="space-y-0.5">
      {#each notes as note (note.id)}
        <li>
          <a
            href="/notes/{note.id}"
            class="flex items-start justify-between gap-2 rounded-lg px-2 py-1.5 transition-colors hover:bg-ink-800/60"
          >
            <span class="min-w-0">
              <span class="block truncate text-[13px] text-ink-100">{note.title}</span>
              <span class="text-[10px] text-ink-500">{relativeTime(note.updated_at)}</span>
            </span>
            {#if note.tags.length > 0}
              <span class="shrink-0 rounded-full bg-ink-800 px-1.5 py-0.5 text-[10px] text-ink-300"
                >{note.tags[0]}</span
              >
            {/if}
          </a>
        </li>
      {/each}
    </ul>
  {:else}
    <ul class="space-y-1.5">
      {#each documents as doc (doc.id)}
        <li class="flex items-start justify-between gap-2">
          <a
            href="/documents/{doc.id}"
            class="min-w-0 truncate text-[13px] text-ink-100 hover:text-accent-400"
            title={doc.filename}>{doc.title}</a
          >
          <ProcessingStatus status={doc.status} stage={doc.processing_stage} compact />
        </li>
      {/each}
    </ul>
  {/if}
</section>
