<script lang="ts">
  /** Renders an answer with live citation chips.
   *
   * Markers whose citation has not arrived yet render dimmed — honest about
   * being unresolved rather than hiding them or faking a source.
   */
  import CitationChip from './CitationChip.svelte';
  import type { Citation } from '$lib/api/client';
  import { segmentAnswer } from '$lib/utils/format';

  let { content, citations = [] }: { content: string; citations?: Citation[] } = $props();

  const byMarker = $derived(new Map(citations.map((c) => [c.marker, c])));
  const segments = $derived(segmentAnswer(content));
</script>

<div class="whitespace-pre-wrap text-[15px] leading-relaxed text-ink-100">
  {#each segments as segment}
    {#if segment.kind === 'text'}
      {segment.value}
    {:else}
      {#each segment.markers as marker}
        <CitationChip {marker} citation={byMarker.get(marker)} />
      {/each}
    {/if}
  {/each}
</div>
