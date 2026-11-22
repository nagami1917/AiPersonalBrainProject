<script lang="ts">
  /**
   * The canvas: nodes, edges, and click-to-focus.
   *
   * Deterministic radial layout rather than a force simulation. A force layout
   * is prettier in motion and worse at its job here: the question a user brings
   * to this view is "what is connected to *this*", and a layout that re-solves
   * itself on every click makes them re-find the node they were looking at.
   *
   * Only edges whose *both* endpoints are drawn are rendered. An edge pointing
   * at a node that is not in the slice is not a connection the user can act on,
   * and drawing it is a lie about the neighbourhood being complete.
   */
  import type { GraphNode, GraphEdge } from '$lib/api/types';

  let {
    nodes = [],
    edges = [],
    seedId = null,
    size = 520,
    onselect
  }: {
    nodes?: GraphNode[];
    edges?: GraphEdge[];
    seedId?: string | null;
    size?: number;
    onselect?: (id: string) => void;
  } = $props();

  const center = $derived(size / 2);

  // The seed sits at the centre; everything else rings it in a stable order
  // (by name) so the picture does not reshuffle between two identical loads.
  const positioned = $derived.by(() => {
    if (nodes.length === 0) return [];
    const root = nodes.find((n) => n.id === seedId) ?? nodes[0];
    const ring = nodes
      .filter((n) => n.id !== root.id)
      .sort((a, b) => a.name.localeCompare(b.name));
    const radius = Math.min(center - 40, ring.length > 8 ? center - 55 : center - 95);

    return [
      { ...root, x: center, y: center, root: true },
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

  const index = $derived(new Map(positioned.map((n) => [n.id, n])));

  const drawnEdges = $derived(
    edges.filter((e) => index.has(e.source) && index.has(e.target))
  );

  function activate(id: string, event: KeyboardEvent) {
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      onselect?.(id);
    }
  }
</script>

{#if positioned.length === 0}
  <p class="px-4 py-16 text-center text-xs text-ink-500">
    This entity has no recorded relations yet.
  </p>
{:else}
  <svg
    viewBox="0 0 {size} {size}"
    class="h-auto w-full"
    role="img"
    aria-label="Entity relationship graph: {nodes.length} entities, {drawnEdges.length} connections"
  >
    {#each drawnEdges as edge (edge.id)}
      {@const from = index.get(edge.source)!}
      {@const to = index.get(edge.target)!}
      <line
        x1={from.x}
        y1={from.y}
        x2={to.x}
        y2={to.y}
        stroke="currentColor"
        class="text-ink-700"
        stroke-width={Math.min(1 + edge.weight * 0.3, 3)}
      >
        <title>{edge.relation} (weight {edge.weight})</title>
      </line>
    {/each}

    {#each positioned as node (node.id)}
      <g
        class="cursor-pointer"
        onclick={() => onselect?.(node.id)}
        onkeydown={(e) => activate(node.id, e)}
        role="button"
        tabindex="0"
        aria-label="{node.name}, {node.mention_count} mentions"
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
