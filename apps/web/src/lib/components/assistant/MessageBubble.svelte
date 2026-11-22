<script lang="ts">
  /**
   * One question and its answer.
   *
   * Three behaviours are deliberate, carried over from the page this replaces:
   *
   *  - "Reading N sources" is shown BEFORE the answer streams, because that is
   *    when retrieval actually happened and it is the honest progress signal.
   *  - Citation rows render from the structured payload, so a marker the server
   *    stripped never appears and a listed source always has a real link.
   *  - An ungrounded answer is styled as a refusal, not as an answer that
   *    happens to be beige.
   */
  import type { Citation, SourcePreview } from '$lib/api/client';
  import AnswerBody from '$lib/components/assistant/AnswerBody.svelte';
  import SourceCitation from './SourceCitation.svelte';
  import { cx } from '$lib/utils/cx';

  export type Turn = {
    id: string;
    question: string;
    answer: string;
    sources: SourcePreview[];
    citations: Citation[];
    grounded: boolean | null;
    streaming: boolean;
  };

  let { turn }: { turn: Turn } = $props();
</script>

<article class="space-y-3">
  <p class="text-sm font-medium text-ink-200">{turn.question}</p>

  {#if turn.sources.length > 0 && turn.streaming && !turn.answer}
    <p class="text-[11px] text-ink-500">
      Reading {turn.sources.length}
      {turn.sources.length === 1 ? 'source' : 'sources'}:
      {turn.sources.map((s) => s.source_title).join(', ')}
    </p>
  {/if}

  <div
    class={cx(
      'rounded-xl border px-4 py-3',
      turn.grounded === false
        ? 'border-amber-900/50 bg-amber-950/20'
        : 'border-ink-800 bg-ink-900/50'
    )}
  >
    {#if turn.answer}
      <AnswerBody content={turn.answer} citations={turn.citations} />
    {:else if turn.streaming}
      <div class="flex items-center gap-2 text-xs text-ink-500">
        <span class="h-1.5 w-1.5 animate-pulse rounded-full bg-accent-500"></span>
        Searching your documents…
      </div>
    {/if}

    {#if turn.grounded === false}
      <p class="mt-2 border-t border-amber-900/40 pt-2 text-[11px] text-amber-400/80">
        No supporting passage was found, so no answer was generated.
      </p>
    {/if}
  </div>

  {#if turn.citations.length > 0}
    <ul class="space-y-1.5">
      {#each turn.citations as citation (citation.marker)}
        <SourceCitation {citation} />
      {/each}
    </ul>
  {/if}
</article>
