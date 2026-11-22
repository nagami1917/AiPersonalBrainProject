<script lang="ts">
  /**
   * Note detail: data and the autosave lifecycle.
   *
   * Everything visual is in `components/notes/` — toolbar, editor, tags,
   * backlinks — so this file holds only what must outlive a child's re-render:
   * the loaded note, the autosaver instance, and the flush-on-destroy that
   * protects unsaved work.
   */
  import { page } from '$app/state';
  import { onMount, onDestroy } from 'svelte';
  import { apiFetch, NexusApiError } from '$lib/api/client';
  import type { Collection, NoteDetail } from '$lib/api/types';
  import { canWrite } from '$lib/api/types';
  import { wsPath, activeWorkspace, activeWorkspaceId } from '$lib/stores/workspace';
  import ErrorNotice from '$lib/components/ErrorNotice.svelte';
  import NoteToolbar from '$lib/components/notes/NoteToolbar.svelte';
  import NoteEditor from '$lib/components/notes/NoteEditor.svelte';
  import TagSelector from '$lib/components/notes/TagSelector.svelte';
  import BacklinksPanel from '$lib/components/notes/BacklinksPanel.svelte';
  import { Autosaver, type SaveState } from '$lib/utils/autosave';

  let note = $state<NoteDetail | null>(null);
  let collections = $state<Collection[]>([]);
  let loading = $state(true);
  let error = $state<{ message: string; code?: string } | null>(null);

  let title = $state('');
  let body = $state('');
  let saveState = $state<SaveState>('idle');
  let saveError = $state<string | null>(null);
  let tags = $state<string[]>([]);

  const writable = $derived(canWrite($activeWorkspace?.role));
  const id = $derived(page.params.id);

  /**
   * Resolvable wiki links for the preview, from the server's own resolution.
   * Re-deriving these here would be a second implementation that could promise
   * a link the save then discards.
   */
  const wikiMap = $derived.by(() => {
    const map = new Map<string, string>();
    for (const link of note?.outgoing_links ?? []) {
      if (link.resolved && link.target_id) map.set(link.target_title.toLowerCase(), link.target_id);
    }
    return map;
  });

  let autosaver: Autosaver | null = null;

  function buildAutosaver() {
    autosaver?.dispose();
    autosaver = new Autosaver({
      delayMs: 1500,
      save: async (text) => {
        if (!note) return;
        await apiFetch(wsPath(`/notes/${note.id}`), {
          method: 'PATCH',
          body: { title: title.trim() || note.title, content: text }
        });
      },
      onState: (state, message) => {
        saveState = state;
        if (state === 'error') saveError = message ?? 'Could not save.';
        else if (state === 'saved') saveError = null;
      }
    });
  }

  async function load() {
    if (!id || !$activeWorkspaceId) return;
    loading = true;
    error = null;
    try {
      const [detail, collectionList] = await Promise.all([
        apiFetch<NoteDetail>(wsPath(`/notes/${id}`)),
        apiFetch<Collection[]>(wsPath('/collections'))
      ]);
      note = detail;
      collections = collectionList;
      title = detail.title;
      body = detail.content ?? '';
      tags = detail.tags;
      buildAutosaver();
      autosaver?.reset(body);
    } catch (err) {
      error =
        err instanceof NexusApiError
          ? { message: err.message, code: err.code }
          : { message: 'Could not load this note.' };
    } finally {
      loading = false;
    }
  }

  onMount(load);

  // Navigating away with a pending debounce must not lose the edit: `flush`
  // writes it, while `dispose` alone would cancel the timer and drop the text.
  onDestroy(() => {
    void autosaver?.flush();
    autosaver?.dispose();
  });

  // Closing the tab fires no Svelte lifecycle; beforeunload is the only hook.
  onMount(() => {
    const warn = (event: BeforeUnloadEvent) => {
      if (saveState === 'dirty' || saveState === 'saving') {
        event.preventDefault();
        event.returnValue = '';
      }
    };
    window.addEventListener('beforeunload', warn);
    return () => window.removeEventListener('beforeunload', warn);
  });

  /** Cheap refresh of just the panels a save can change. */
  async function reloadLinks() {
    if (!note) return;
    try {
      note = await apiFetch<NoteDetail>(wsPath(`/notes/${note.id}`));
      tags = note.tags;
    } catch {
      /* a failed refresh leaves the previous, still-accurate view */
    }
  }

  async function saveNow() {
    if (!note) return;
    await autosaver?.saveNow(body);
    if (!saveError) await reloadLinks();
  }

  async function remove() {
    if (!note) return;
    try {
      await apiFetch(wsPath(`/notes/${note.id}`), { method: 'DELETE' });
      window.location.href = '/notes';
    } catch (err) {
      error =
        err instanceof NexusApiError
          ? { message: err.message, code: err.code }
          : { message: 'Could not delete the note.' };
    }
  }

  async function setCollection(value: string) {
    if (!note || !writable) return;
    try {
      await apiFetch(wsPath(`/notes/${note.id}`), {
        method: 'PATCH',
        body: { collection_id: value || null }
      });
      await load();
    } catch (err) {
      error =
        err instanceof NexusApiError
          ? { message: err.message, code: err.code }
          : { message: 'Could not move that note.' };
    }
  }
</script>

<svelte:head><title>{note?.title ?? 'Note'} · NEXUS</title></svelte:head>

<div class="space-y-5">
  <a href="/notes" class="text-xs text-ink-500 hover:text-ink-300">← Notes</a>

  {#if error}<ErrorNotice message={error.message} code={error.code} />{/if}

  {#if loading}
    <div class="skeleton h-40 w-full"></div>
  {:else if note}
    <NoteToolbar
      bind:title
      status={note.status}
      stage={note.processing_stage}
      updatedAt={note.updated_at}
      chunkCount={note.chunk_count}
      embeddingModel={note.embedding_model}
      {saveState}
      editable={writable}
      saving={saveState === 'saving'}
      onsave={saveNow}
      ondelete={remove}
    />

    {#if saveError}
      <p class="rounded-lg border border-red-900/60 bg-red-950/30 px-3 py-2 text-xs text-red-300">
        {saveError}
      </p>
    {/if}

    {#if note.summary}
      <p class="rounded-xl border border-ink-800 bg-ink-900/40 px-4 py-3 text-sm text-ink-300">
        {note.summary}
      </p>
    {/if}

    {#if note.status === 'failed' && note.error_message}
      <p class="rounded-lg border border-red-900/60 bg-red-950/30 px-3 py-2 text-xs text-red-300">
        {note.error_message}
      </p>
    {/if}

    <div class="flex flex-wrap items-center gap-2">
      <TagSelector
        noteId={note.id}
        bind:tags
        editable={writable}
        onerror={(message) => (error = { message })}
      />
      {#if writable && collections.length > 0}
        <select
          class="rounded-full border border-ink-700 bg-ink-900 px-2 py-0.5 text-[11px] text-ink-300 focus:border-accent-500 focus:outline-none"
          value={note.collection_id ?? ''}
          onchange={(e) => setCollection((e.currentTarget as HTMLSelectElement).value)}
          aria-label="Collection"
        >
          <option value="">No collection</option>
          {#each collections as collection (collection.id)}
            <option value={collection.id}>{collection.name}</option>
          {/each}
        </select>
      {/if}
    </div>

    <NoteEditor
      bind:body
      editable={writable}
      wikiLinks={wikiMap}
      oninput={() => autosaver?.change(body)}
    />

    <BacklinksPanel
      noteTitle={note.title}
      backlinks={note.backlinks}
      outgoing={note.outgoing_links}
    />

    {#if !writable}
      <p class="text-[11px] text-ink-500">
        You have read-only access to this workspace, so this note cannot be edited.
      </p>
    {/if}
  {:else}
    <p class="text-sm text-ink-400">This note does not exist in this workspace.</p>
  {/if}
</div>
