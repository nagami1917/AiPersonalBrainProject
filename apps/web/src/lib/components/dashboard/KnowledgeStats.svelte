<script lang="ts">
  /**
   * Two bands of real counts, both from one `/stats` response.
   *
   * `cards` is the top row (notes, documents, collections, connections) and the
   * lower band is the "what has the system actually built" row (passages,
   * entities, links, note links). They are separate props rather than one list
   * because they answer different questions and sit in different places on the
   * page — but they must come from the same fetch, or the numbers can disagree
   * with themselves on screen.
   */
  import type { WorkspaceStats } from '$lib/api/types';

  type Card = { label: string; value: number; hint: string; href?: string };

  let {
    stats = null,
    loading = false,
    variant = 'cards'
  }: { stats?: WorkspaceStats | null; loading?: boolean; variant?: 'cards' | 'detail' } = $props();

  const cards = $derived<Card[]>(
    stats
      ? [
          {
            label: 'Notes',
            value: stats.notes,
            hint: `${stats.notes_ready} searchable`,
            href: '/notes'
          },
          {
            label: 'Documents',
            value: stats.documents,
            hint:
              stats.documents_processing > 0
                ? `${stats.documents_processing} processing`
                : `${stats.documents_ready} ready`,
            href: '/documents'
          },
          {
            label: 'Collections',
            value: stats.collections,
            hint: `${stats.tags} tags`,
            href: '/collections'
          },
          {
            label: 'Connections',
            value: stats.links,
            hint: `${stats.entities} entities`,
            href: '/graph'
          }
        ]
      : []
  );

  const detail = $derived(
    stats
      ? [
          { label: 'Indexed passages', value: stats.chunks, of: 'searchable' },
          { label: 'Entities', value: stats.entities, of: 'people, projects, ideas' },
          { label: 'Connections', value: stats.links, of: 'entity relationships' },
          { label: 'Note links', value: stats.note_links, of: 'in note bodies' }
        ]
      : []
  );
</script>

{#if variant === 'cards'}
  <section class="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
    {#each cards as card (card.label)}
      <a href={card.href} class="card group p-4 transition-colors hover:border-ink-700">
        {#if loading}
          <div class="skeleton h-3 w-16"></div>
          <div class="skeleton mt-3 h-7 w-12"></div>
          <div class="skeleton mt-2 h-2.5 w-20"></div>
        {:else}
          <p class="text-xs uppercase tracking-wide text-ink-500">{card.label}</p>
          <p class="mt-1 text-2xl font-semibold tabular-nums text-ink-50">{card.value}</p>
          <p class="mt-0.5 text-[11px] text-ink-500">{card.hint}</p>
        {/if}
      </a>
    {/each}
  </section>
{:else}
  <section class="card p-4">
    <div class="flex items-center justify-between">
      <h2 class="text-sm font-medium text-ink-200">Knowledge statistics</h2>
      <span class="text-[11px] text-ink-500">live from this workspace</span>
    </div>
    {#if loading || !stats}
      <div class="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {#each [1, 2, 3, 4] as i (i)}<div class="skeleton h-14 w-full"></div>{/each}
      </div>
    {:else}
      <div class="mt-3 grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
        {#each detail as item (item.label)}
          <div class="rounded-lg bg-ink-900/60 px-3 py-2">
            <p class="text-[10px] uppercase tracking-wide text-ink-500">{item.label}</p>
            <p class="text-lg font-semibold tabular-nums text-ink-50">{item.value}</p>
            <p class="text-[10px] text-ink-500">{item.of}</p>
          </div>
        {/each}
      </div>
    {/if}
  </section>
{/if}
