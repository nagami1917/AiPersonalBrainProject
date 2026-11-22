<script lang="ts">
  /**
   * The selected entity: what it is, what it links to, and where it was said.
   *
   * The mentions list is the part that makes the graph trustworthy. A graph of
   * extracted entities is a *claim about* the documents, and a claim the user
   * cannot check is one they will learn to distrust. Every mention links back
   * to the note or document it came from.
   */
  import type { EntityDetail } from '$lib/api/types';
  import { sourceHref, sourceLabel } from '$lib/utils/sources';

  let {
    entity = null,
    loading = false,
    onselect
  }: {
    entity?: EntityDetail | null;
    loading?: boolean;
    onselect?: (id: string) => void;
  } = $props();
</script>

<section class="card space-y-3 p-4">
  {#if loading}
    <div class="skeleton h-4 w-32"></div>
    <div class="skeleton h-3 w-full"></div>
    <div class="skeleton h-3 w-2/3"></div>
  {:else if !entity}
    <p class="py-6 text-center text-xs text-ink-500">
      Select an entity to see what it connects to and where it appears.
    </p>
  {:else}
    <header class="space-y-1">
      <div class="flex items-center justify-between gap-2">
        <h2 class="truncate text-sm font-medium text-ink-100">{entity.name}</h2>
        <span class="shrink-0 rounded-full bg-ink-800 px-2 py-0.5 text-[10px] text-ink-300">
          {entity.entity_type}
        </span>
      </div>
      {#if entity.description}
        <p class="text-[11px] text-ink-400">{entity.description}</p>
      {/if}
      <p class="text-[10px] text-ink-500">
        {entity.mention_count} mention{entity.mention_count === 1 ? '' : 's'} across your sources
      </p>
    </header>

    {#if entity.links.length > 0}
      <div>
        <h3 class="mb-1 text-[10px] uppercase tracking-wide text-ink-500">Connections</h3>
        <ul class="space-y-0.5">
          {#each entity.links as link (link.id)}
            <li>
              <button
                class="flex w-full items-center justify-between gap-2 rounded px-1.5 py-1 text-left text-[11px] text-ink-300 hover:bg-ink-800"
                onclick={() => onselect?.(link.entity.id)}
              >
                <span class="min-w-0 truncate">
                  <span class="text-ink-500">{link.direction === 'out' ? '→' : '←'}</span>
                  {link.entity.name}
                </span>
                <span class="shrink-0 text-[10px] text-ink-600">{link.relation}</span>
              </button>
            </li>
          {/each}
        </ul>
      </div>
    {/if}

    {#if entity.mentions.length > 0}
      <div>
        <h3 class="mb-1 text-[10px] uppercase tracking-wide text-ink-500">Mentioned in</h3>
        <ul class="space-y-0.5">
          {#each entity.mentions as mention (mention.chunk_id)}
            <li class="flex items-center justify-between gap-2 px-1.5 py-0.5 text-[11px]">
              <a
                href={sourceHref(mention.source_kind, mention.source_id)}
                class="min-w-0 truncate text-ink-300 hover:text-accent-400"
                >{mention.source_title}</a
              >
              <span class="shrink-0 text-[10px] text-ink-600">{sourceLabel(mention.source_kind)}</span>
            </li>
          {/each}
        </ul>
      </div>
    {/if}
  {/if}
</section>
