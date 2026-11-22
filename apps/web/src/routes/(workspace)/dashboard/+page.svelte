<script lang="ts">
  import { onMount } from 'svelte';
  import { apiFetch, NexusApiError } from '$lib/api/client';
  import { wsPath, activeWorkspaceId } from '$lib/stores/workspace';
  import type { DocumentSummary, Entity, Job, SearchResult } from '$lib/api/types';
  import StatusPill from '$lib/components/StatusPill.svelte';
  import ErrorNotice from '$lib/components/ErrorNotice.svelte';
  import { relativeTime } from '$lib/utils/format';

  let documents = $state<DocumentSummary[]>([]);
  let jobs = $state<Job[]>([]);
  let entities = $state<Entity[]>([]);
  let loading = $state(true);
  let error = $state<{ message: string; code?: string } | null>(null);

  const processing = $derived(documents.filter((d) => d.status === 'processing' || d.status === 'pending'));
  const ready = $derived(documents.filter((d) => d.status === 'ready'));
  const failed = $derived(documents.filter((d) => d.status === 'failed'));

  onMount(async () => {
    if (!$activeWorkspaceId) return;
    try {
      const [docs, jobList, ents] = await Promise.all([
        apiFetch<{ items: DocumentSummary[] }>(wsPath('/documents?limit=6')),
        apiFetch<Job[]>(wsPath('/jobs?limit=5')),
        apiFetch<Entity[]>(wsPath('/graph/entities?limit=8'))
      ]);
      documents = docs.items;
      jobs = jobList;
      entities = ents;
    } catch (err) {
      error =
        err instanceof NexusApiError
          ? { message: err.message, code: err.code }
          : { message: 'Could not load your dashboard.' };
    } finally {
      loading = false;
    }
  });

  const stats = $derived([
    { label: 'Documents', value: documents.length, hint: 'in your knowledge base' },
    { label: 'Ready', value: ready.length, hint: 'searchable and citable' },
    { label: 'Processing', value: processing.length, hint: 'working in the background' },
    { label: 'Failed', value: failed.length, hint: failed.length ? 'needs attention' : 'nothing broken' }
  ]);
</script>

<svelte:head><title>Dashboard · NEXUS</title></svelte:head>

<div class="space-y-8">
  <header>
    <h1 class="text-xl font-semibold tracking-tight text-ink-50">Dashboard</h1>
    <p class="mt-1 text-sm text-ink-400">
      Everything here is grounded in your own documents. Ask a question and you will get sources,
      not guesses.
    </p>
  </header>

  {#if error}
    <ErrorNotice message={error.message} code={error.code} />
  {/if}

  <section class="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
    {#each stats as stat}
      <div class="card p-4">
        <p class="text-xs uppercase tracking-wide text-ink-500">{stat.label}</p>
        {#if loading}
          <div class="skeleton mt-2 h-7 w-12"></div>
        {:else}
          <p class="mt-1 text-2xl font-semibold text-ink-50">{stat.value}</p>
        {/if}
        <p class="mt-0.5 text-[11px] text-ink-500">{stat.hint}</p>
      </div>
    {/each}
  </section>

  <section class="space-y-3">
    <div class="flex items-center justify-between">
      <h2 class="text-sm font-medium text-ink-200">Recent documents</h2>
      <a href="/documents" class="text-xs text-accent-400 hover:underline">View all</a>
    </div>

    {#if loading}
      <div class="space-y-2">
        {#each [1, 2, 3] as i}
          <div class="skeleton h-14 w-full"></div>
        {/each}
      </div>
    {:else if documents.length === 0}
      <div class="card p-6 text-center">
        <p class="text-sm text-ink-300">Nothing here yet.</p>
        <p class="mt-1 text-xs text-ink-500">Add a PDF or write a note to get started.</p>
        <a class="btn-primary mt-4" href="/documents">Add a document</a>
      </div>
    {:else}
      <ul class="divide-y divide-ink-800 rounded-xl border border-ink-800">
        {#each documents as doc (doc.id)}
          <li class="flex items-center justify-between gap-4 px-4 py-3">
            <div class="min-w-0">
              <a href="/documents/{doc.id}" class="block truncate text-sm text-ink-100 hover:text-accent-400">
                {doc.title}
              </a>
              <p class="text-[11px] text-ink-500">
                Document · {relativeTime(doc.updated_at)}
              </p>
            </div>
            <StatusPill status={doc.status} stage={doc.processing_stage} />
          </li>
        {/each}
      </ul>
    {/if}
  </section>

  <div class="grid gap-6 lg:grid-cols-2">
    <section class="space-y-3">
      <h2 class="text-sm font-medium text-ink-200">Processing activity</h2>
      {#if jobs.length === 0}
        <p class="rounded-lg border border-ink-800 px-4 py-6 text-center text-xs text-ink-500">
          No jobs yet. Uploading a document queues one.
        </p>
      {:else}
        <ul class="space-y-1.5">
          {#each jobs as job (job.id)}
            <li class="flex items-center justify-between rounded-lg bg-ink-900/60 px-3 py-2 text-xs">
              <span class="text-ink-300">{job.type}</span>
              <span
                class="font-mono"
                class:text-emerald-400={job.status === 'succeeded'}
                class:text-red-400={job.status === 'failed' || job.status === 'dead'}
                class:text-ink-400={job.status === 'queued' || job.status === 'running'}
              >
                {job.status}
                {#if job.attempts > 1}· try {job.attempts}{/if}
              </span>
            </li>
          {/each}
        </ul>
      {/if}
    </section>

    <section class="space-y-3">
      <div class="flex items-center justify-between">
        <h2 class="text-sm font-medium text-ink-200">Connected topics</h2>
        <a href="/graph" class="text-xs text-accent-400 hover:underline">Explore graph</a>
      </div>
      {#if entities.length === 0}
        <p class="rounded-lg border border-ink-800 px-4 py-6 text-center text-xs text-ink-500">
          Topics appear once documents finish processing.
        </p>
      {:else}
        <div class="flex flex-wrap gap-1.5">
          {#each entities as entity (entity.id)}
            <a
              href="/graph?entity={entity.id}"
              class="rounded-full bg-ink-800 px-2.5 py-1 text-xs text-ink-200 hover:bg-ink-700"
            >
              {entity.name}
              <span class="text-ink-500">{entity.mention_count}</span>
            </a>
          {/each}
        </div>
      {/if}
    </section>
  </div>
</div>
