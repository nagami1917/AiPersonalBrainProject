<script lang="ts">
  /**
   * Retrieval-only search: no LLM. This is also the debugging surface for RAG
   * quality, so `matched_arm` is shown — it explains WHY a result surfaced.
   */
  import { apiFetch, NexusApiError } from '$lib/api/client';
  import type { SearchResult } from '$lib/api/types';
  import { sourceHref, sourceLabel } from '$lib/utils/sources';
  import { wsPath, activeWorkspaceId } from '$lib/stores/workspace';
  import ErrorNotice from '$lib/components/ErrorNotice.svelte';

  let query = $state('');
  let mode = $state<'hybrid' | 'semantic' | 'keyword'>('hybrid');
  let results = $state<SearchResult[]>([]);
  let candidates = $state<{ vector: number; keyword: number } | null>(null);
  let busy = $state(false);
  let searched = $state(false);
  let error = $state<{ message: string; code?: string } | null>(null);

  async function search() {
    if (!query.trim() || busy || !$activeWorkspaceId) return;
    busy = true;
    error = null;
    try {
      const response = await apiFetch<{
        results: SearchResult[];
        total_candidates: { vector: number; keyword: number };
      }>(wsPath('/search'), { method: 'POST', body: { query: query.trim(), mode, limit: 20 } });
      results = response.results;
      candidates = response.total_candidates;
      searched = true;
    } catch (err) {
      error =
        err instanceof NexusApiError
          ? { message: err.message, code: err.code }
          : { message: 'Search failed.' };
    } finally {
      busy = false;
    }
  }

  const armLabel: Record<string, string> = {
    both: 'vector + keyword',
    vector: 'semantic',
    keyword: 'exact terms'
  };
</script>

<svelte:head><title>Search · NEXUS</title></svelte:head>

<div class="space-y-6">
  <header>
    <h1 class="text-xl font-semibold tracking-tight text-ink-50">Search</h1>
    <p class="mt-1 text-sm text-ink-400">
      Raw retrieval, no model involved. Useful for checking that what you expect to be findable
      actually is.
    </p>
  </header>

  <form
    class="flex flex-wrap gap-2"
    onsubmit={(e) => {
      e.preventDefault();
      void search();
    }}
  >
    <input class="input min-w-48 flex-1" placeholder="Search your knowledge…" bind:value={query} />
    <select class="input w-auto" bind:value={mode}>
      <option value="hybrid">Hybrid</option>
      <option value="semantic">Semantic</option>
      <option value="keyword">Keyword</option>
    </select>
    <button class="btn-primary shrink-0" type="submit" disabled={busy}>
      {busy ? 'Searching…' : 'Search'}
    </button>
  </form>

  {#if error}
    <ErrorNotice message={error.message} code={error.code} />
  {/if}

  {#if candidates}
    <p class="text-[11px] text-ink-500">
      Candidates: {candidates.vector} from the vector arm, {candidates.keyword} from the keyword
      arm, fused with rank fusion.
    </p>
  {/if}

  {#if searched && results.length === 0}
    <p class="rounded-xl border border-ink-800 px-4 py-8 text-center text-sm text-ink-400">
      Nothing matched. That usually means the document has not finished processing, or the wording
      is quite different from what you saved.
    </p>
  {:else}
    <ul class="space-y-2">
      {#each results as result (result.chunk_id)}
        <li class="card px-4 py-3">
          <div class="flex items-start justify-between gap-3">
            <a
              href={sourceHref(result.source_kind, result.source_id)}
              class="text-sm font-medium text-ink-100 hover:text-accent-400"
            >
              {result.source_title}
            </a>
            <div class="flex shrink-0 items-center gap-2 text-[10px] uppercase tracking-wide">
              <span class="rounded bg-ink-800 px-1.5 py-0.5 text-ink-400">
                {sourceLabel(result.source_kind)}
              </span>
              <span class="rounded bg-ink-800 px-1.5 py-0.5 text-ink-400">
                {armLabel[result.matched_arm] ?? result.matched_arm}
              </span>
              <span class="font-mono text-ink-500">{result.score.toFixed(2)}</span>
            </div>
          </div>
          {#if result.heading_path}
            <p class="mt-0.5 text-[11px] text-ink-500">
              {result.heading_path}{#if result.page} · p.{result.page}{/if}
            </p>
          {/if}
          <p class="mt-1.5 line-clamp-3 text-[13px] leading-relaxed text-ink-300">{result.snippet}</p>
        </li>
      {/each}
    </ul>
  {/if}
</div>
