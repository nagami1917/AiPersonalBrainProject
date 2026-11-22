<script lang="ts">
  /**
   * Knowledge graph page: composition and data only.
   *
   * A previous version of this page called `/v1/graph/entities/{id}/neighbors`,
   * which did not exist on the server — it rendered "Could not load the graph"
   * for every user and every workspace. The endpoint now exists, and this page
   * calls it through `wsPath` so it is workspace-scoped like everything else
   * (the flat `/v1/graph` path is for clients that resolve a workspace
   * themselves).
   */
  import { onMount } from 'svelte';
  import { page } from '$app/state';
  import { apiFetch, NexusApiError } from '$lib/api/client';
  import { wsPath, activeWorkspaceId } from '$lib/stores/workspace';
  import type { Entity, EntityDetail, GraphSlice } from '$lib/api/types';
  import ErrorNotice from '$lib/components/ErrorNotice.svelte';
  import EmptyState from '$lib/components/EmptyState.svelte';
  import KnowledgeGraph from '$lib/components/graph/KnowledgeGraph.svelte';
  import GraphControls from '$lib/components/graph/GraphControls.svelte';
  import NodeDetails from '$lib/components/graph/NodeDetails.svelte';

  let entities = $state<Entity[]>([]);
  let slice = $state<GraphSlice | null>(null);
  let detail = $state<EntityDetail | null>(null);
  let selected = $state<string | null>(null);
  let query = $state('');
  let depth = $state(1);
  let loading = $state(true);
  let sliceLoading = $state(false);
  let error = $state<{ message: string; code?: string } | null>(null);

  async function loadSlice(id: string, nextDepth = depth) {
    if (!$activeWorkspaceId) return;
    sliceLoading = true;
    selected = id;
    error = null;
    try {
      // Two requests, deliberately parallel: the canvas needs the slice, the
      // side panel needs the entity's own links and mentions.
      const [nextSlice, nextDetail] = await Promise.all([
        apiFetch<GraphSlice>(wsPath(`/graph/entities/${id}/neighbors?depth=${nextDepth}`)),
        apiFetch<EntityDetail>(wsPath(`/graph/entities/${id}`))
      ]);
      slice = nextSlice;
      detail = nextDetail;
    } catch (err) {
      error =
        err instanceof NexusApiError
          ? { message: err.message, code: err.code }
          : { message: 'Could not load this part of the graph.' };
    } finally {
      sliceLoading = false;
    }
  }

  async function loadInitial(entityId: string | null) {
    if (!$activeWorkspaceId) return;
    loading = true;
    error = null;
    try {
      const list = await apiFetch<Entity[]>(wsPath('/graph/entities?limit=60'));
      entities = list;
      const seed = entityId ?? list[0]?.id ?? null;
      if (seed) await loadSlice(seed);
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
    void loadInitial(page.url.searchParams.get('entity'));
  });
</script>

<svelte:head><title>Graph · NEXUS</title></svelte:head>

<div class="space-y-6">
  <header>
    <h1 class="text-xl font-semibold tracking-tight text-ink-50">Graph</h1>
    <p class="mt-1 text-sm text-ink-400">
      Entities extracted from your documents, and how they co-occur. Derived from passages — never
      a second source of truth.
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
      <GraphControls
        {entities}
        {selected}
        bind:query
        bind:depth
        loading={sliceLoading}
        onselect={(id) => loadSlice(id)}
        ondepth={(level) => selected && loadSlice(selected, level)}
      />

      <div class="space-y-4">
        <div class="card overflow-hidden">
          {#if sliceLoading && !slice}
            <div class="skeleton h-96 w-full"></div>
          {:else}
            <KnowledgeGraph
              nodes={slice?.nodes ?? []}
              edges={slice?.edges ?? []}
              seedId={slice?.seed_id ?? null}
              onselect={(id) => loadSlice(id)}
            />
          {/if}
        </div>

        <NodeDetails entity={detail} loading={sliceLoading} onselect={(id) => loadSlice(id)} />
      </div>
    </div>
  {/if}
</div>
