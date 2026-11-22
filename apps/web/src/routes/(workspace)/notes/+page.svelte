<script lang="ts">
  import { onMount } from 'svelte';
  import { apiFetch, NexusApiError } from '$lib/api/client';
  import type { NoteSummary } from '$lib/api/types';
  import { wsPath, activeWorkspaceId } from '$lib/stores/workspace';
  import ProcessingStatus from '$lib/components/documents/ProcessingStatus.svelte';
  import ErrorNotice from '$lib/components/ErrorNotice.svelte';
  import EmptyState from '$lib/components/EmptyState.svelte';
  import { relativeTime } from '$lib/utils/format';

  let notes = $state<NoteSummary[]>([]);
  let loading = $state(true);
  let error = $state<{ message: string; code?: string } | null>(null);
  let creating = $state(false);
  let title = $state('');
  let body = $state('');

  async function load() {
    if (!$activeWorkspaceId) return;
    try {
      const response = await apiFetch<{ items: NoteSummary[] }>(wsPath('/notes'));
      notes = response.items;
    } catch (err) {
      error =
        err instanceof NexusApiError
          ? { message: err.message, code: err.code }
          : { message: 'Could not load notes.' };
    } finally {
      loading = false;
    }
  }

  onMount(load);

  async function create() {
    if (!title.trim() || !body.trim()) return;
    error = null;
    try {
      await apiFetch(wsPath('/notes'), {
        method: 'POST',
        body: { title: title.trim(), content: body.trim() }
      });
      title = '';
      body = '';
      creating = false;
      await load();
    } catch (err) {
      error =
        err instanceof NexusApiError
          ? { message: err.message, code: err.code }
          : { message: 'Could not save the note.' };
    }
  }
</script>

<svelte:head><title>Notes · NEXUS</title></svelte:head>

<div class="space-y-6">
  <header class="flex items-end justify-between gap-3">
    <div>
      <h1 class="text-xl font-semibold tracking-tight text-ink-50">Notes</h1>
      <p class="mt-1 text-sm text-ink-400">
        Notes are first-class knowledge: same indexing, same citations as uploaded files.
      </p>
    </div>
    <button class="btn-primary" onclick={() => (creating = !creating)}>
      {creating ? 'Cancel' : 'New note'}
    </button>
  </header>

  {#if error}
    <ErrorNotice message={error.message} code={error.code} />
  {/if}

  {#if creating}
    <form
      class="card space-y-3 p-4"
      onsubmit={(e) => {
        e.preventDefault();
        void create();
      }}
    >
      <div>
        <label class="label" for="note-title">Title</label>
        <input id="note-title" class="input" bind:value={title} maxlength={500} required />
      </div>
      <div>
        <label class="label" for="note-body">Body</label>
        <textarea id="note-body" class="input min-h-40" bind:value={body} required></textarea>
        <p class="mt-1 text-[11px] text-ink-500">
          Markdown headings become citation paths, not just formatting.
        </p>
      </div>
      <div class="flex justify-end">
        <button class="btn-primary" type="submit">Save note</button>
      </div>
    </form>
  {/if}

  {#if loading}
    <div class="space-y-2">
      {#each [1, 2] as i}<div class="skeleton h-14 w-full"></div>{/each}
    </div>
  {:else if notes.length === 0}
    <EmptyState title="No notes yet" description="Write one and it becomes searchable immediately." />
  {:else}
    <ul class="divide-y divide-ink-800 rounded-xl border border-ink-800">
      {#each notes as note (note.id)}
        <li class="flex items-center justify-between gap-4 px-4 py-3">
          <div class="min-w-0">
            <a href="/notes/{note.id}" class="block truncate text-sm text-ink-100 hover:text-accent-400">
              {note.title}
            </a>
            <p class="flex items-center gap-1.5 text-[11px] text-ink-500">
              <span>updated {relativeTime(note.updated_at)}</span>
              {#if note.tags.length > 0}
                <span>·</span>
                <span class="truncate">{note.tags.join(', ')}</span>
              {/if}
            </p>
          </div>
          <ProcessingStatus status={note.status} stage={note.processing_stage} compact />
        </li>
      {/each}
    </ul>
  {/if}
</div>
