<script lang="ts">
  /** A citation marker that proves a claim, and shows its evidence on hover. */
  import type { Citation } from '$lib/api/client';
  import { cx } from '$lib/utils/cx';
  import { sourceHref, sourceLabel } from '$lib/utils/sources';

  let { marker, citation }: { marker: number; citation?: Citation } = $props();
  let open = $state(false);

  // A pending marker (still streaming) must look pending, not broken.
  const resolved = $derived(citation !== undefined);
  const label = $derived(
    citation ? `${sourceLabel(citation.source_kind)} · ${citation.source_title}` : ''
  );
</script>

<span class="relative inline-block">
  <button
    type="button"
    class={cx('cite-chip', !resolved && 'opacity-40')}
    aria-label={resolved ? `Source ${marker}: ${label}` : `Source ${marker} loading`}
    onmouseenter={() => (open = true)}
    onmouseleave={() => (open = false)}
    onfocus={() => (open = true)}
    onblur={() => (open = false)}
  >
    {marker}
  </button>

  {#if open && resolved}
    <span
      class="absolute bottom-full left-1/2 z-20 mb-2 w-72 -translate-x-1/2 rounded-lg border border-ink-700 bg-ink-850 p-3 text-left text-xs shadow-xl"
      role="tooltip"
    >
      <span class="block text-[10px] uppercase tracking-wide text-ink-500">
        {sourceLabel(citation!.source_kind)}
      </span>
      <a
        href={sourceHref(citation!.source_kind, citation!.source_id)}
        class="block font-medium text-ink-100 hover:text-accent-400"
      >
        {citation?.source_title}
      </a>
      {#if citation?.page}
        <span class="mt-0.5 block text-ink-400">page {citation.page}</span>
      {/if}
      {#if citation?.heading_path}
        <span class="mt-0.5 block text-ink-500">{citation.heading_path}</span>
      {/if}
      <span class="mt-2 block border-l-2 border-cite-500/40 pl-2 italic leading-relaxed text-ink-300">
        {citation?.quote}
      </span>
    </span>
  {/if}
</span>
