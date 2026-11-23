<script lang="ts">
  import { onMount } from 'svelte';
  import { apiFetch, NexusApiError } from '$lib/api/client';
  import type { NoteSummary } from '$lib/api/types';
  import { wsPath, activeWorkspaceId } from '$lib/stores/workspace';
  import ProcessingStatus from '$lib/components/documents/ProcessingStatus.svelte';
  import ErrorNotice from '$lib/components/ErrorNotice.svelte';
  import EmptyState from '$lib/components/EmptyState.svelte';
  import TrashPanel from '$lib/components/TrashPanel.svelte';
  import { relativeTime } from '$lib/utils/format';

  let notes = $state<NoteSummary[]>([]);
  let loading = $state(true);
  let error = $state<{ message: string; code?: string } | null>(null);
  let creating = $state(false);
  let title = $state('');
  let body = $state('');
  let view = $state<'active' | 'trash'>('active');
  let query = $state('');
  let searching = $state(false);

  /**
   * The search box filters through the API (`?q=`) rather than in the browser.
   *
   * Client-side filtering would only search the page already loaded, so a note
   * on page 2 would appear missing — the failure that teaches people not to
   * trust search. This is the substring filter, not semantic search: it costs no
   * embedding call and stays instant while typing.
   */
  async function load(q = query) {
    if (!$activeWorkspaceId) return;
    try {
      const params = q.trim() ? `?q=${encodeURIComponent(q.trim())}` : '';
      const response = await apiFetch<{ items: NoteSummary[] }>(wsPath(`/notes${params}`));
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

  onMount(() => void load());

  // Debounced so typing does not issue a request per keystroke. 250ms is short
  // enough to feel immediate and long enough that a fast typist sends one.
  let searchTimer: ReturnType<typeof setTimeout> | null = null;
  function onSearchInput(value: string) {
    query = value;
    if (searchTimer) clearTimeout(searchTimer);
    searching = true;
    searchTimer = setTimeout(() => {
      searching = false;
      void load(value);
    }, 250);
  }

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

  <div class="flex flex-wrap items-center gap-2">
    <div class="flex rounded-lg border border-ink-800 p-0.5">
      <button
        class="rounded-md px-3 py-1 text-xs transition-colors"
        class:bg-ink-800={view === 'active'}
        class:text-ink-100={view === 'active'}
        class:text-ink-500={view !== 'active'}
        onclick={() => (view = 'active')}
      >
        Notes
      </button>
      <button
        class="rounded-md px-3 py-1 text-xs transition-colors"
        class:bg-ink-800={view === 'trash'}
        class:text-ink-100={view === 'trash'}
        class:text-ink-500={view !== 'trash'}
        onclick={() => (view = 'trash')}
      >
        Trash
      </button>
    </div>
    {#if view === 'active'}
      <input
        class="input max-w-xs flex-1"
        type="search"
        placeholder="Search notes…"
        value={query}
        oninput={(e) => onSearchInput((e.currentTarget as HTMLInputElement).value)}
        aria-label="Search notes by title or text"
      />
      {#if searching}
        <span class="text-[10px] text-ink-600">searching…</span>
      {/if}
    {/if}
  </div>

  {#if view === 'trash'}
    <TrashPanel
      kind="notes"
      onrestored={() => void load()}
    />
  {:else}

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
    {#if query.trim()}
      <EmptyState
        title="No notes match that search"
        description="Search looks at note titles and text. Semantic search is on the Search page."
      />
    {:else}
      <EmptyState title="No notes yet" description="Write one and it becomes searchable immediately." />
    {/if}
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
{/if}
</div>
