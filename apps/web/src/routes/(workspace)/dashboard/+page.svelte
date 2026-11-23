<script lang="ts">
  /**
   * Welcome page: composition and one parallel fetch.
   *
   * The four panels read as one screen, so they load as one: a page that paints
   * its cards first and its lists two seconds later reads as broken. Everything
   * visual lives in `components/dashboard/`.
   */
  import { onMount } from 'svelte';
  import { apiFetch, NexusApiError } from '$lib/api/client';
  import {
    wsPath,
    activeWorkspace,
    activeWorkspaceId,
    workspacesLoading,
    workspacesError
  } from '$lib/stores/workspace';
  import { canWrite } from '$lib/api/types';
  import type {
    DocumentSummary,
    NoteSummary,
    SuggestedConnection,
    WorkspaceStats
  } from '$lib/api/types';
  import ErrorNotice from '$lib/components/ErrorNotice.svelte';
  import WelcomeCard from '$lib/components/dashboard/WelcomeCard.svelte';
  import RecentNotes from '$lib/components/dashboard/RecentNotes.svelte';
  import KnowledgeStats from '$lib/components/dashboard/KnowledgeStats.svelte';
  import KnowledgeGraphMini from '$lib/components/dashboard/KnowledgeGraphMini.svelte';
  import QuickCapture from '$lib/components/dashboard/QuickCapture.svelte';

  let stats = $state<WorkspaceStats | null>(null);
  let notes = $state<NoteSummary[]>([]);
  let documents = $state<DocumentSummary[]>([]);
  let suggestions = $state<SuggestedConnection[]>([]);
  let loading = $state(true);
  let error = $state<{ message: string; code?: string } | null>(null);

  const writable = $derived(canWrite($activeWorkspace?.role));
  const empty = $derived(!loading && stats !== null && stats.notes === 0 && stats.documents === 0);

  async function load() {
    // No workspace id yet is not a dead end on its own: the id arrives
    // asynchronously from `loadWorkspaces()`. Returning early here without
    // clearing `loading` is what left the page spinning forever when the
    // workspace fetch failed, so the failure case is handled in the template
    // against `$workspacesError` instead of being indistinguishable from
    // "still starting up".
    if (!$activeWorkspaceId) {
      loading = false;
      return;
    }
    loading = true;
    error = null;
    try {
      const [statsResult, notesResult, docsResult, suggestionsResult] = await Promise.all([
        apiFetch<WorkspaceStats>(wsPath('/stats')),
        apiFetch<{ items: NoteSummary[] }>(wsPath('/notes?limit=5')),
        apiFetch<{ items: DocumentSummary[] }>(wsPath('/documents?limit=5')),
        apiFetch<SuggestedConnection[]>(wsPath('/suggestions?limit=3'))
      ]);
      stats = statsResult;
      notes = notesResult.items;
      documents = docsResult.items;
      suggestions = suggestionsResult;
    } catch (err) {
      error =
        err instanceof NexusApiError
          ? { message: err.message, code: err.code }
          : { message: 'Could not load your workspace.' };
    } finally {
      loading = false;
    }
  }

  onMount(load);
</script>

<svelte:head><title>Welcome · NEXUS</title></svelte:head>

<div class="space-y-6">
  <WelcomeCard
    {empty}
    noteCount={stats?.notes ?? 0}
    documentCount={stats?.documents ?? 0}
  />

  {#if error}
    <ErrorNotice message={error.message} code={error.code} />
  {/if}

  {#if $workspacesError}
    <!--
      The workspace list is the first thing every page needs. If it cannot be
      fetched nothing else on this page can work, so the cause is shown here
      rather than five panels each failing separately.
    -->
    <ErrorNotice
      message={$workspacesError.message}
      code={$workspacesError.code ?? 'workspaces_unavailable'}
    />
  {:else if !$workspacesLoading && !$activeWorkspaceId}
    <ErrorNotice
      message="Your account has no workspace yet. Reload the page, or sign in again to finish setup."
      code="no_workspace"
    />
  {/if}

  {#if !empty}
    <KnowledgeStats {stats} {loading} variant="cards" />

    <div class="grid gap-4 lg:grid-cols-5">
      <div class="lg:col-span-2">
        <RecentNotes title="Recent notes" href="/notes" {loading} notes={notes} emptyLabel="No notes yet." />
      </div>
      <div class="lg:col-span-1">
        <RecentNotes
          title="Recent documents"
          href="/documents"
          {loading}
          documents={documents}
          emptyLabel="No documents yet."
        />
      </div>
      <section class="card space-y-2 p-4 lg:col-span-2">
        <div class="flex items-center justify-between">
          <h2 class="text-sm font-medium text-ink-200">Knowledge graph</h2>
          <a href="/graph" class="text-[11px] text-accent-400 hover:underline">Explore</a>
        </div>
        {#if loading}
          <div class="skeleton h-40 w-full"></div>
        {:else if !stats || stats.entities === 0}
          <p class="py-10 text-center text-xs text-ink-500">
            Connections appear once your documents finish processing.
          </p>
        {:else}
          <KnowledgeGraphMini />
        {/if}
      </section>
    </div>

    <KnowledgeStats {stats} {loading} variant="detail" />

    <div class="grid gap-4 lg:grid-cols-2">
      {#if writable}
        <QuickCapture oncaptured={() => void load()} />
      {/if}

      <section class="card space-y-2 p-4">
        <div class="flex items-center justify-between">
          <h2 class="text-sm font-medium text-ink-200">Suggested connections</h2>
          <span class="text-[11px] text-ink-500">unlinked pairs</span>
        </div>
        {#if loading}
          <div class="space-y-2">
            {#each [1, 2] as i (i)}<div class="skeleton h-12 w-full"></div>{/each}
          </div>
        {:else if suggestions.length === 0}
          <p class="py-6 text-center text-xs text-ink-500">
            Nothing to suggest yet. Notes that share tags or content show up here until you link
            them.
          </p>
        {:else}
          <ul class="space-y-2">
            {#each suggestions as item (`${item.source.id}:${item.target.id}`)}
              <li class="rounded-lg bg-ink-900/60 px-3 py-2">
                <p class="flex flex-wrap items-center gap-1.5 text-[13px]">
                  <a href="/notes/{item.source.id}" class="text-ink-100 hover:text-accent-400"
                    >{item.source.title}</a
                  >
                  <span class="text-ink-600">↔</span>
                  <a href="/notes/{item.target.id}" class="text-ink-100 hover:text-accent-400"
                    >{item.target.title}</a
                  >
                </p>
                <!-- The reason is shown, not implied: a suggestion with no stated
                     basis reads as a random guess and gets ignored. -->
                <p class="mt-0.5 text-[10px] text-ink-500">
                  {item.reason}
                  {#if item.similarity !== null}
                    · {Math.round(item.similarity * 100)}% similar
                  {/if}
                </p>
              </li>
            {/each}
          </ul>
        {/if}
      </section>
    </div>
  {/if}
</div>
