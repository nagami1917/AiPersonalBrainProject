<script lang="ts">
  /**
   * One cited passage under an answer.
   *
   * The marker is the same visual as the inline chip, so the connection between
   * "the [2] in the sentence" and "this row" is learnable rather than guessed.
   * `filename` is shown for documents because that is what the user recognises
   * they uploaded; a note falls back to its title.
   */
  import type { Citation } from '$lib/api/client';
  import { sourceHref, sourceLabel } from '$lib/utils/sources';

  let { citation }: { citation: Citation } = $props();
</script>

<li class="flex gap-2 text-[11px] text-ink-400">
  <span class="cite-chip">{citation.marker}</span>
  <span class="min-w-0">
    <span class="text-[10px] uppercase tracking-wide text-ink-500">
      {sourceLabel(citation.source_kind)}
    </span>
    <a
      href={sourceHref(citation.source_kind, citation.source_id)}
      class="text-ink-200 hover:text-accent-400"
    >
      {citation.source_kind === 'document' && citation.filename
        ? citation.filename
        : citation.source_title}
    </a>
    {#if citation.page}<span class="text-ink-500"> · p.{citation.page}</span>{/if}
    {#if citation.score > 0}
      <span class="text-ink-600"> · {Math.round(citation.score * 100)}% match</span>
    {/if}
    {#if citation.quote}
      <span class="mt-0.5 block truncate italic text-ink-500">“{citation.quote}”</span>
    {/if}
  </span>
</li>
