<script lang="ts">
  /**
   * Welcome — the first screen, built from the reference layout.
   *
   * The reference's composition is kept (stat row, recent + graph, then the
   * lower band) because it answers four questions in the order someone arriving
   * actually asks them: *what is in here*, *what did I touch last*, *what is it
   * connected to*, and *what should I do now*.
   *
   * What is deliberately not copied from the reference:
   *  - "Life Overview" (health, habits, finance, goals) — this product has no
   *    such data, and inventing widgets with fake numbers would be a lie in the
   *    exact place a user forms their first impression. That band is Knowledge
   *    statistics instead: real counts from one query.
   *  - Zero-state cards never show 0/0/0/0. With no content the page says so
   *    once and offers the two ways to start.
   */
  import { onMount } from 'svelte';
  import { apiFetch, NexusApiError } from '$lib/api/client';
  import { wsPath, activeWorkspace, activeWorkspaceId } from '$lib/stores/workspace';
  import { canWrite } from '$lib/api/types';
  import type {
    DocumentSummary,
    NoteSummary,
    SuggestedConnection,
    WorkspaceStats
  } from '$lib/api/types';
  import StatusPill from '$lib/components/StatusPill.svelte';
  import ErrorNotice from '$lib/components/ErrorNotice.svelte';
  import QuickCapture from '$lib/components/QuickCapture.svelte';
  import KnowledgeGraphMini from '$lib/components/KnowledgeGraphMini.svelte';
  import { relativeTime } from '$lib/utils/format';

  let stats = $state<WorkspaceStats | null>(null);
  let notes = $state<NoteSummary[]>([]);
  let documents = $state<DocumentSummary[]>([]);
  let suggestions = $state<SuggestedConnection[]>([]);
  let loading = $state(true);
  let error = $state<{ message: string; code?: string } | null>(null);
  /** Bumped after a capture so the lists reflect it without a page reload. */
  let refreshKey = $state(0);

  const writable = $derived(canWrite($activeWorkspace?.role));
  const empty = $derived(
    !loading && stats !== null && stats.notes === 0 && stats.documents === 0
  );

  async function load() {
    if (!$activeWorkspaceId) return;
    loading = true;
    error = null;
    try {
      // One round trip per panel, all in parallel: the page should paint once,
      // not in the order the network happens to answer.
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

  // Re-fetch when the active workspace changes, so switching tenants never
  // leaves the previous workspace's numbers on screen.
  $effect(() => {
    if ($activeWorkspaceId) void refreshKey;
  });

  const statCards = $derived(
    stats
      ? [
          { label: 'Notes', value: stats.notes, hint: `${stats.notes_ready} searchable`, href: '/notes' },
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

  function onCaptured() {
    refreshKey += 1;
    void load();
  }
</script>

<svelte:head><title>Welcome · NEXUS</title></svelte:head>

<div class="space-y-6">
  <header class="flex flex-wrap items-end justify-between gap-3">
    <div>
      <h1 class="text-2xl font-semibold tracking-tight text-ink-50">Welcome</h1>
      <p class="mt-1 text-sm text-ink-400">
        {$activeWorkspace?.name ?? 'Your workspace'} · your knowledge, connected.
      </p>
    </div>
    <div class="flex items-center gap-2">
      <a class="btn-ghost text-xs" href="/search">Search</a>
      <a class="btn-primary text-xs" href="/assistant">Ask the assistant</a>
    </div>
  </header>

  {#if error}
    <ErrorNotice message={error.message} code={error.code} />
  {/if}

  {#if empty}
    <!-- A brand-new workspace: one clear next step beats four empty stat cards. -->
    <section class="card px-6 py-10 text-center">
      <h2 class="text-sm font-medium text-ink-100">This workspace is empty</h2>
      <p class="mx-auto mt-1 max-w-md text-sm text-ink-400">
        Add a document or write a note and NEXUS starts building your knowledge graph. Answers
        cite what you add, so nothing works until something is here.
      </p>
      <div class="mt-5 flex flex-wrap justify-center gap-2">
        <a class="btn-primary" href="/documents">Upload a document</a>
        <a class="btn-ghost" href="/notes">Write a note</a>
      </div>
    </section>
  {:else}
    <!-- Stat row -->
    <section class="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
      {#each statCards as card (card.label)}
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

    <!-- Recent notes / documents, side by side with the graph -->
    <div class="grid gap-4 lg:grid-cols-5">
      <section class="card space-y-3 p-4 lg:col-span-2">
        <div class="flex items-center justify-between">
          <h2 class="text-sm font-medium text-ink-200">Recent notes</h2>
          <a href="/notes" class="text-[11px] text-accent-400 hover:underline">All</a>
        </div>
        {#if loading}
          <div class="space-y-2">
            {#each [1, 2, 3] as i}<div class="skeleton h-9 w-full"></div>{/each}
          </div>
        {:else if notes.length === 0}
          <p class="py-6 text-center text-xs text-ink-500">No notes yet.</p>
        {:else}
          <ul class="space-y-0.5">
            {#each notes as note (note.id)}
              <li>
                <a
                  href="/notes/{note.id}"
                  class="flex items-start justify-between gap-2 rounded-lg px-2 py-1.5 transition-colors hover:bg-ink-800/60"
                >
                  <span class="min-w-0">
                    <span class="block truncate text-[13px] text-ink-100">{note.title}</span>
                    <span class="text-[10px] text-ink-500">{relativeTime(note.updated_at)}</span>
                  </span>
                  {#if note.tags.length > 0}
                    <span class="shrink-0 rounded-full bg-ink-800 px-1.5 py-0.5 text-[10px] text-ink-300"
                      >{note.tags[0]}</span
                    >
                  {/if}
                </a>
              </li>
            {/each}
          </ul>
        {/if}
      </section>

      <section class="card space-y-3 p-4 lg:col-span-1">
        <div class="flex items-center justify-between">
          <h2 class="text-sm font-medium text-ink-200">Recent documents</h2>
          <a href="/documents" class="text-[11px] text-accent-400 hover:underline">All</a>
        </div>
        {#if loading}
          <div class="space-y-2">
            {#each [1, 2, 3] as i}<div class="skeleton h-9 w-full"></div>{/each}
          </div>
        {:else if documents.length === 0}
          <p class="py-6 text-center text-xs text-ink-500">No documents yet.</p>
        {:else}
          <ul class="space-y-1.5">
            {#each documents as doc (doc.id)}
              <li class="flex items-start justify-between gap-2">
                <a
                  href="/documents/{doc.id}"
                  class="min-w-0 truncate text-[13px] text-ink-100 hover:text-accent-400"
                  title={doc.filename}>{doc.title}</a
                >
                <StatusPill status={doc.status} stage={doc.processing_stage} />
              </li>
            {/each}
          </ul>
        {/if}
      </section>

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

    <!-- Lower band: real workspace statistics, not invented life metrics -->
    <section class="card p-4">
      <div class="flex items-center justify-between">
        <h2 class="text-sm font-medium text-ink-200">Knowledge statistics</h2>
        <span class="text-[11px] text-ink-500">live from this workspace</span>
      </div>
      {#if loading || !stats}
        <div class="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          {#each [1, 2, 3, 4] as i}<div class="skeleton h-14 w-full"></div>{/each}
        </div>
      {:else}
        <div class="mt-3 grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
          {#each [
            { label: 'Indexed passages', value: stats.chunks, of: 'searchable' },
            { label: 'Entities', value: stats.entities, of: 'people, projects, ideas' },
            { label: 'Connections', value: stats.links, of: 'entity relationships' },
            { label: 'Note links', value: stats.note_links, of: 'in note bodies' }
          ] as item (item.label)}
            <div class="rounded-lg bg-ink-900/60 px-3 py-2">
              <p class="text-[10px] uppercase tracking-wide text-ink-500">{item.label}</p>
              <p class="text-lg font-semibold tabular-nums text-ink-50">{item.value}</p>
              <p class="text-[10px] text-ink-500">{item.of}</p>
            </div>
          {/each}
        </div>
      {/if}
    </section>

    <!-- Quick capture + suggested connections -->
    <div class="grid gap-4 lg:grid-cols-2">
      {#if writable}
        <QuickCapture oncaptured={onCaptured} />
      {/if}

      <section class="card space-y-2 p-4">
        <div class="flex items-center justify-between">
          <h2 class="text-sm font-medium text-ink-200">Suggested connections</h2>
          <span class="text-[11px] text-ink-500">unlinked pairs</span>
        </div>
        {#if loading}
          <div class="space-y-2">
            {#each [1, 2] as i}<div class="skeleton h-12 w-full"></div>{/each}
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
