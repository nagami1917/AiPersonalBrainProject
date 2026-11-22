<script lang="ts">
  /**
   * The document's extracted text.
   *
   * Shown as extracted text rather than an embedded PDF viewer on purpose: what
   * matters is *what the system can cite* — a passage the user cannot see in
   * this view but that ends up in an answer is the confusing case, and this is
   * the view that prevents it.
   *
   * `raw_text` is the same text the chunker ran on, so a heading that looks
   * wrong here is a heading that will look wrong in citations too.
   */
  import type { DocumentDetail } from '$lib/api/types';

  let { document }: { document: DocumentDetail } = $props();

  let expanded = $state(false);
</script>

<section class="space-y-2">
  <div class="flex items-center justify-between">
    <h2 class="text-xs font-medium uppercase tracking-wide text-ink-500">Extracted text</h2>
    {#if document.raw_text && document.raw_text.length > 4000}
      <button class="text-[11px] text-accent-400 hover:underline" onclick={() => (expanded = !expanded)}>
        {expanded ? 'Collapse' : 'Show all'}
      </button>
    {/if}
  </div>

  {#if document.raw_text}
    <div
      class="overflow-y-auto rounded-xl border border-ink-800 bg-ink-900/40 p-4"
      class:max-h-[28rem]={!expanded}
      class:max-h-[70vh]={expanded}
    >
      <pre class="whitespace-pre-wrap font-sans text-[13px] leading-relaxed text-ink-300">{expanded
          ? document.raw_text
          : document.raw_text.slice(0, 4000)}{#if !expanded && document.raw_text.length > 4000}…{/if}</pre>
    </div>
    <p class="text-[10px] text-ink-600">
      {document.chunk_count} passages indexed from this text.
    </p>
  {:else if document.status === 'failed'}
    <p class="rounded-lg border border-red-900/60 bg-red-950/30 px-4 py-4 text-center text-xs text-red-300">
      Text could not be extracted, so this document cannot be cited yet.
    </p>
  {:else if document.status === 'processing' || document.status === 'pending'}
    <p class="rounded-lg border border-ink-800 px-4 py-6 text-center text-xs text-ink-500">
      Text is still being extracted. This panel fills in when that finishes.
    </p>
  {:else}
    <p class="rounded-lg border border-ink-800 px-4 py-6 text-center text-xs text-ink-500">
      No text was extracted from this file. It may be a scanned document with no text layer.
    </p>
  {/if}
</section>
