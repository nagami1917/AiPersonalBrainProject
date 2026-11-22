<script lang="ts">
  /**
   * The entity list, filter and depth control for the graph.
   *
   * Depth is capped at 2 and the cap is explained in the UI rather than
   * silently enforced: hop count is what turns a knowledge graph into a
   * hairball, and a user who wonders why they cannot go deeper deserves the
   * reason rather than a dead control.
   */
  import type { Entity } from '$lib/api/types';
  import { cx } from '$lib/utils/cx';

  let {
    entities = [],
    selected = null,
    query = $bindable(''),
    depth = $bindable(1),
    loading = false,
    onselect,
    ondepth
  }: {
    entities?: Entity[];
    selected?: string | null;
    query?: string;
    depth?: number;
    loading?: boolean;
    onselect: (id: string) => void;
    ondepth?: (depth: number) => void;
  } = $props();

  const filtered = $derived(
    query.trim()
      ? entities.filter((e) => e.name.toLowerCase().includes(query.trim().toLowerCase()))
      : entities
  );

  const TYPES: Record<string, string> = {
    person: 'bg-pink-400',
    organization: 'bg-amber-400',
    project: 'bg-sky-400',
    concept: 'bg-violet-400',
    place: 'bg-emerald-400',
    event: 'bg-orange-400',
    other: 'bg-ink-400'
  };
</script>

<aside class="space-y-3">
  <div>
    <label class="label" for="graph-search">Find an entity</label>
    <input id="graph-search" class="input text-xs" placeholder="Filter…" bind:value={query} />
  </div>

  {#if ondepth}
    <div>
      <span class="label">Depth</span>
      <div class="flex gap-1">
        {#each [1, 2] as level (level)}
          <button
            class={cx(
              'flex-1 rounded px-2 py-1 text-xs transition-colors',
              depth === level ? 'bg-ink-700 text-ink-50' : 'text-ink-400 hover:bg-ink-800'
            )}
            onclick={() => {
              depth = level;
              ondepth?.(level);
            }}
            aria-pressed={depth === level}
          >
            {level} hop{level === 1 ? '' : 's'}
          </button>
        {/each}
      </div>
      <p class="mt-1 text-[10px] text-ink-600">
        Capped at 2 — beyond that every graph looks the same.
      </p>
    </div>
  {/if}

  <div>
    <h2 class="mb-2 text-xs font-medium uppercase tracking-wide text-ink-500">Entities</h2>
    {#if loading}
      <div class="space-y-1">
        {#each [1, 2, 3, 4] as i (i)}<div class="skeleton h-6 w-full"></div>{/each}
      </div>
    {:else if filtered.length === 0}
      <p class="px-1 text-[11px] text-ink-500">No entity matches that filter.</p>
    {:else}
      <ul class="max-h-[28rem] space-y-0.5 overflow-y-auto">
        {#each filtered as entity (entity.id)}
          <li>
            <button
              class={cx(
                'flex w-full items-center gap-1.5 rounded-lg px-2 py-1.5 text-left text-xs transition-colors',
                selected === entity.id
                  ? 'bg-ink-800 text-ink-100'
                  : 'text-ink-300 hover:bg-ink-800/60'
              )}
              onclick={() => onselect(entity.id)}
              aria-current={selected === entity.id ? 'true' : undefined}
            >
              <!-- Type is a colour, not a word: the label costs width the names need. -->
              <span
                class="h-1.5 w-1.5 shrink-0 rounded-full {TYPES[entity.entity_type] ?? TYPES.other}"
                title={entity.entity_type}
              ></span>
              <span class="min-w-0 flex-1 truncate">{entity.name}</span>
              <span class="shrink-0 tabular-nums text-ink-500">{entity.mention_count}</span>
            </button>
          </li>
        {/each}
      </ul>
    {/if}
  </div>
</aside>
