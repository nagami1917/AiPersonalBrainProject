<script lang="ts">
  /**
   * A small radial snapshot of the knowledge graph, for the Welcome page.
   *
   * Same data as the Graph page (`/graph` returns nodes and edges in one
   * response), but a fixed, deterministic layout: a preview that jiggles on
   * every visit is noise, and the point here is the *shape* of the knowledge —
   * how many things cluster around what — not exploration. The Graph page owns
   * interaction.
   */
  import { onMount } from 'svelte';
  import { apiFetch, NexusApiError } from '$lib/api/client';
  import { wsPath } from '$lib/stores/workspace';

  type Node = { id: string; name: string; entity_type: string; mention_count: number };
  type Edge = { id: string; source_id: string; target_id: string; relation: string; weight: number };

  let nodes = $state<Node[]>([]);
  let edges = $state<Edge[]>([]);
  let loading = $state(true);
  let error = $state<string | null>(null);

  const SIZE = 240;
  const CENTER = SIZE / 2;

  /** Colour by entity type, so clusters are legible without reading labels. */
  const TYPE_COLOR: Record<string, string> = {
    person: '#f472b6',
    organization: '#fbbf24',
    project: '#38bdf8',
    concept: '#a78bfa',
    place: '#34d399',
    event: '#fb923c',
    other: '#94a3b8'
  };

  const positioned = $derived.by(() => {
    if (nodes.length === 0) return [];
    // The most-mentioned entity anchors the centre; the rest ring around it in
    // name order so the picture is stable between loads.
    const sorted = [...nodes].sort((a, b) => b.mention_count - a.mention_count);
    const [hub, ...ring] = sorted;
    const radius = Math.min(CENTER - 26, 88);
    return [
      { ...hub, x: CENTER, y: CENTER, color: TYPE_COLOR[hub.entity_type] ?? TYPE_COLOR.other },
      ...ring.map((node, index) => {
        const angle = (index / Math.max(ring.length, 1)) * Math.PI * 2 - Math.PI / 2;
        return {
          ...node,
          x: CENTER + Math.cos(angle) * radius,
          y: CENTER + Math.sin(angle) * radius,
          color: TYPE_COLOR[node.entity_type] ?? TYPE_COLOR.other
        };
      })
    ];
  });

  const index = $derived(new Map(positioned.map((n) => [n.id, n])));

  /** Only edges whose both ends are drawn — an edge to a missing node is a lie. */
  const drawnEdges = $derived(
    edges
      .filter((e) => index.has(e.source_id) && index.has(e.target_id))
      .map((e) => ({
        ...e,
        from: index.get(e.source_id)!,
        to: index.get(e.target_id)!
      }))
  );

  async function load() {
    loading = true;
    error = null;
    try {
      const payload = await apiFetch<{ nodes: Node[]; edges: Edge[] }>(wsPath('/graph?limit=12'));
      nodes = payload.nodes;
      edges = payload.edges;
    } catch (err) {
      error =
        err instanceof NexusApiError ? err.message : 'Could not load the graph preview.';
    } finally {
      loading = false;
    }
  }

  onMount(load);
</script>

{#if loading}
  <div class="skeleton mx-auto h-52 w-52 rounded-full"></div>
{:else if error}
  <p class="py-10 text-center text-xs text-ink-500">{error}</p>
{:else if positioned.length === 0}
  <p class="py-10 text-center text-xs text-ink-500">No entities yet.</p>
{:else}
  <svg
    viewBox="0 0 {SIZE} {SIZE}"
    class="mx-auto h-52 w-52"
    role="img"
    aria-label="Knowledge graph preview: {nodes.length} entities, {drawnEdges.length} connections"
  >
    {#each drawnEdges as edge (edge.id)}
      <line
        x1={edge.from.x}
        y1={edge.from.y}
        x2={edge.to.x}
        y2={edge.to.y}
        stroke={edge.from.color}
        stroke-opacity="0.25"
        stroke-width={Math.min(1 + edge.weight * 0.15, 2.5)}
      />
    {/each}
    {#each positioned as node (node.id)}
      <g>
        <circle cx={node.x} cy={node.y} r={4 + Math.min(node.mention_count, 12) * 0.7} fill={node.color} fill-opacity="0.85" />
        <text
          x={node.x}
          y={node.y - 9}
          text-anchor="middle"
          class="fill-ink-400"
          style="font-size:7px"
        >
          {node.name.length > 16 ? `${node.name.slice(0, 15)}…` : node.name}
        </text>
      </g>
    {/each}
  </svg>
  <p class="mt-1 text-center text-[10px] text-ink-500">
    {nodes.length} entities · {drawnEdges.length} shown connections
  </p>
{/if}
