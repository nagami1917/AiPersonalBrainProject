<script lang="ts">
  /**
   * Knowledge graph: a 1–2 hop slice, capped server-side.
   *
   * Rendered as a simple radial layout rather than a force simulation — the
   * point is "what is connected to this", which reads better deterministically.
   */
  import { onMount } from 'svelte';
  import { page } from '$app/state';
  import { apiFetch, NexusApiError } from '$lib/api/client';
  import type { Entity } from '$lib/api/types';
  import ErrorNotice from '$lib/components/ErrorNotice.svelte';
  import EmptyState from '$lib/components/EmptyState.svelte';

  type Node = { id: string; name: string; entity_type: string; mention_count: number };
  type Edge = { source: string; target: string; relation: string; weight: number };

  let entities = $state<Entity[]>([]);
  let nodes = $state<Node[]>([]);
  let edges = $state<Edge[]>([]);
  let selected = $state<string | null>(null);
  let loading = $state(true);
  let error = $state<{ message: string; code?: string } | null>(null);

  const size = 520;
  const center = size / 2;

  /** Deterministic radial positions: stable across renders, no jitter. */
  const positioned = $derived.by(() => {
    if (nodes.length === 0) return [] as Array<Node & { x: number; y: number; root: boolean }>;
    const rootId = selected ?? nodes[0].id;
    const ring = nodes.filter((n) => n.id !== rootId);
    const radius = ring.length > 6 ? center - 60 : center - 110;
    return [
      { ...nodes.find((n) => n.id === rootId)!, x: center, y: center, root: true },
      ...ring.map((node, index) => {
        const angle = (index / Math.max(ring.length, 1)) * Math.PI * 2 - Math.PI / 2;
        return {
          ...node,
          x: center + Math.cos(angle) * radius,
          y: center + Math.sin(angle) * radius,
          root: false
        };
      })
    ];
  });

  const positions = $derived(new Map(positioned.map((n) => [n.id, n])));

  async function load(entityId: string | null) {
    try {
      const list = await apiFetch<Entity[]>('/v1/graph/entities?limit=40');
      entities = list;
      const seed = entityId ?? list[0]?.id ?? null;
      selected = seed;
      if (seed) {
        const slice = await apiFetch<{ nodes: Node[]; edges: Edge[] }>(
          `/v1/graph/entities/${seed}/neighbors?depth=1`
        );
        nodes = slice.nodes;
        edges = slice.edges;
      }
    } catch (err) {
      error =
        err instanceof NexusApiError
          ? { message: err.message, code: err.code }
          : { message: 'Could not load the graph.' };
    } finally {
      loading = false;
    }
  }

  onMount(() => {
    void load(page.url.searchParams.get('entity'));
  });
</script>

<svelte:head><title>Graph · NEXUS</title></svelte:head>

<div class="space-y-6">
  <header>
    <h1 class="text-xl font-semibold tracking-tight text-ink-50">Graph</h1>
    <p class="mt-1 text-sm text-ink-400">
      Entities extracted from your documents, and how they co-occur. Derived from passages — never a
      second source of truth.
    </p>
  </header>

  {#if error}
    <ErrorNotice message={error.message} code={error.code} />
  {/if}

  {#if loading}
    <div class="skeleton h-96 w-full"></div>
  {:else if entities.length === 0}
    <EmptyState
      title="No entities yet"
      description="Entities are extracted after a document finishes processing. Add a document with named people, projects or concepts."
    />
  {:else}
    <div class="grid gap-6 lg:grid-cols-[240px_1fr]">
      <aside class="space-y-1">
        <h2 class="mb-2 text-xs font-medium uppercase tracking-wide text-ink-500">Entities</h2>
        {#each entities as entity (entity.id)}
          <button
            class="flex w-full items-center justify-between rounded-lg px-2 py-1.5 text-left text-xs transition-colors"
            class:bg-ink-800={selected === entity.id}
            class:text-ink-100={selected === entity.id}
            class:text-ink-300={selected !== entity.id}
            onclick={() => load(entity.id)}
          >
            <span class="truncate">{entity.name}</span>
            <span class="text-ink-500">{entity.mention_count}</span>
          </button>
        {/each}
      </aside>

      <div class="card overflow-hidden">
        {#if nodes.length === 0}
          <p class="px-4 py-16 text-center text-xs text-ink-500">
            This entity has no recorded relations yet.
          </p>
        {:else}
          <svg viewBox="0 0 {size} {size}" class="h-auto w-full" role="img" aria-label="Entity relationship graph">
            {#each edges as edge}
              {@const from = positions.get(edge.source)}
              {@const to = positions.get(edge.target)}
              {#if from && to}
                <line
                  x1={from.x}
                  y1={from.y}
                  x2={to.x}
                  y2={to.y}
                  stroke="currentColor"
                  class="text-ink-700"
                  stroke-width={Math.min(1 + edge.weight * 0.3, 3)}
                />
              {/if}
            {/each}

            {#each positioned as node (node.id)}
              <g
                class="cursor-pointer"
                onclick={() => load(node.id)}
                role="button"
                tabindex="0"
                onkeydown={(e) => e.key === 'Enter' && load(node.id)}
              >
                <circle
                  cx={node.x}
                  cy={node.y}
                  r={node.root ? 12 : 5 + Math.min(node.mention_count, 5)}
                  class={node.root ? 'fill-accent-500' : 'fill-ink-500'}
                />
                <text
                  x={node.x}
                  y={node.y - (node.root ? 18 : 12)}
                  text-anchor="middle"
                  class={node.root ? 'fill-ink-100' : 'fill-ink-400'}
                  font-size="11"
                >
                  {node.name.length > 22 ? node.name.slice(0, 21) + '…' : node.name}
                </text>
              </g>
            {/each}
          </svg>
        {/if}
      </div>
    </div>
  {/if}
</div>
