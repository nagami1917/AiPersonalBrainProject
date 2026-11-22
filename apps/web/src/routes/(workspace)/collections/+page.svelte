<script lang="ts">
  import { onMount } from 'svelte';
  import { apiFetch, NexusApiError } from '$lib/api/client';
  import type { Collection, Tag } from '$lib/api/types';
  import ErrorNotice from '$lib/components/ErrorNotice.svelte';
  import EmptyState from '$lib/components/EmptyState.svelte';

  let collections = $state<Collection[]>([]);
  let tags = $state<Tag[]>([]);
  let loading = $state(true);
  let error = $state<{ message: string; code?: string } | null>(null);
  let name = $state('');

  async function load() {
    try {
      const [cols, tagList] = await Promise.all([
        apiFetch<Collection[]>('/v1/collections'),
        apiFetch<Tag[]>('/v1/tags')
      ]);
      collections = cols;
      tags = tagList;
    } catch (err) {
      error =
        err instanceof NexusApiError
          ? { message: err.message, code: err.code }
          : { message: 'Could not load collections.' };
    } finally {
      loading = false;
    }
  }

  onMount(load);

  async function create() {
    if (!name.trim()) return;
    try {
      await apiFetch('/v1/collections', { method: 'POST', body: { name: name.trim() } });
      name = '';
      await load();
    } catch (err) {
      error =
        err instanceof NexusApiError
          ? { message: err.message, code: err.code }
          : { message: 'Could not create the collection.' };
    }
  }
</script>

<svelte:head><title>Collections · NEXUS</title></svelte:head>

<div class="space-y-6">
  <header>
    <h1 class="text-xl font-semibold tracking-tight text-ink-50">Collections</h1>
    <p class="mt-1 text-sm text-ink-400">
      Folders are for humans, not for retrieval. Deleting one never deletes your documents.
    </p>
  </header>

  {#if error}
    <ErrorNotice message={error.message} code={error.code} />
  {/if}

  <form
    class="flex gap-2"
    onsubmit={(e) => {
      e.preventDefault();
      void create();
    }}
  >
    <input class="input" placeholder="New collection name" bind:value={name} maxlength={120} />
    <button class="btn-primary shrink-0" type="submit">Add</button>
  </form>

  {#if loading}
    <div class="skeleton h-24 w-full"></div>
  {:else if collections.length === 0}
    <EmptyState
      title="No collections"
      description="Collections group documents. They are optional — nothing is hidden from search by being uncategorized."
    />
  {:else}
    <ul class="grid gap-2 sm:grid-cols-2">
      {#each collections as collection (collection.id)}
        <li class="card flex items-center justify-between px-4 py-3">
          <div>
            <p class="text-sm text-ink-100">{collection.name}</p>
            {#if collection.description}
              <p class="text-[11px] text-ink-500">{collection.description}</p>
            {/if}
          </div>
          <span class="text-xs text-ink-500">{collection.document_count}</span>
        </li>
      {/each}
    </ul>
  {/if}

  {#if tags.length > 0}
    <section class="space-y-2">
      <h2 class="text-xs font-medium uppercase tracking-wide text-ink-500">Tags</h2>
      <div class="flex flex-wrap gap-1.5">
        {#each tags as tag (tag.id)}
          <span class="rounded-full bg-ink-800 px-2.5 py-1 text-xs text-ink-200">
            {tag.name} <span class="text-ink-500">{tag.document_count}</span>
          </span>
        {/each}
      </div>
    </section>
  {/if}
</div>
