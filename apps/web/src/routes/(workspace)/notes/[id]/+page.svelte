<script lang="ts">
  /**
   * Note detail — read, edit, autosave, tag, and see both directions of links.
   *
   * The editor is a split view rather than a toggle: Markdown's value is that
   * the source is readable, and hiding it behind a mode switch makes formatting
   * a guess. Preview updates as you type, from the same parser that renders the
   * saved note, so what you see is what you get back.
   *
   * Autosave is off until you actually edit, and the header always says which
   * state you are in. A silent autosave that failed is worse than no autosave —
   * the user closes the tab believing their work is safe.
   */
  import { page } from '$app/state';
  import { onMount, onDestroy } from 'svelte';
  import { apiFetch, NexusApiError } from '$lib/api/client';
  import type { Collection, NoteDetail } from '$lib/api/types';
  import { canWrite } from '$lib/api/types';
  import { wsPath, activeWorkspace, activeWorkspaceId } from '$lib/stores/workspace';
  import StatusPill from '$lib/components/StatusPill.svelte';
  import ErrorNotice from '$lib/components/ErrorNotice.svelte';
  import MarkdownView from '$lib/components/MarkdownView.svelte';
  import { Autosaver, SAVE_LABEL, type SaveState } from '$lib/utils/autosave';
  import { extractWikiLinks } from '$lib/utils/markdown';
  import { relativeTime } from '$lib/utils/format';
  import { cx } from '$lib/utils/cx';

  let note = $state<NoteDetail | null>(null);
  let collections = $state<Collection[]>([]);
  let loading = $state(true);
  let error = $state<{ message: string; code?: string } | null>(null);

  let editing = $state(false);
  let title = $state('');
  let body = $state('');
  let saveState = $state<SaveState>('idle');
  let saveError = $state<string | null>(null);

  let tagInput = $state('');
  let tags = $state<string[]>([]);
  let tagBusy = $state(false);

  const writable = $derived(canWrite($activeWorkspace?.role));
  const id = $derived(page.params.id);

  /**
   * Resolvable wiki links for the preview. Built from this note's own outgoing
   * links, because the server already resolved them — the preview must not
   * re-implement resolution and risk disagreeing with the save.
   */
  const wikiMap = $derived.by(() => {
    const map = new Map<string, string>();
    for (const link of note?.outgoing_links ?? []) {
      if (link.resolved && link.target_id) {
        map.set(link.target_title.toLowerCase(), link.target_id);
      }
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

  // Navigating away with a pending debounce must not lose the edit. `flush`
  // writes it; `dispose` alone would cancel the timer and drop the text.
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

  function onBodyInput(value: string) {
    if (!editing) editing = true;
    autosaver?.change(value);
  }

  async function saveNow() {
    if (!note) return;
    await autosaver?.saveNow(body);
    if (saveError) return;
    // A save changes the resolved links, so the panel is stale until reloaded.
    await reloadLinks();
  }

  /** Cheap refresh: only the link panels and status, not the whole page. */
  async function reloadLinks() {
    if (!note) return;
    try {
      const detail = await apiFetch<NoteDetail>(wsPath(`/notes/${note.id}`));
      note = detail;
      tags = detail.tags;
    } catch {
      /* a failed refresh leaves the previous (still correct-at-the-time) view */
    }
  }

  async function addTag() {
    const name = tagInput.trim().toLowerCase();
    if (!name || !note || tagBusy || tags.includes(name)) return;
    tagBusy = true;
    try {
      await apiFetch(wsPath(`/notes/${note.id}`), {
        method: 'PATCH',
        body: { tags: [...tags, name] }
      });
      tags = [...tags, name];
      tagInput = '';
    } catch (err) {
      error =
        err instanceof NexusApiError
          ? { message: err.message, code: err.code }
          : { message: 'Could not add that tag.' };
    } finally {
      tagBusy = false;
    }
  }

  async function removeTag(name: string) {
    if (!note || tagBusy) return;
    tagBusy = true;
    try {
      await apiFetch(wsPath(`/notes/${note.id}`), {
        method: 'PATCH',
        body: { tags: tags.filter((t) => t !== name) }
      });
      tags = tags.filter((t) => t !== name);
    } catch (err) {
      error =
        err instanceof NexusApiError
          ? { message: err.message, code: err.code }
          : { message: 'Could not remove that tag.' };
    } finally {
      tagBusy = false;
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

  /** Links the draft body promises but the saved note does not have yet. */
  const draftLinks = $derived(extractWikiLinks(body));
  const savedTargets = $derived(
    new Set((note?.outgoing_links ?? []).map((l) => l.target_title.toLowerCase()))
  );
  const unresolvedCount = $derived(
    (note?.outgoing_links ?? []).filter((l) => !l.resolved).length
  );
</script>

<svelte:head><title>{note?.title ?? 'Note'} · NEXUS</title></svelte:head>

<div class="space-y-5">
  <a href="/notes" class="text-xs text-ink-500 hover:text-ink-300">← Notes</a>

  {#if error}<ErrorNotice message={error.message} code={error.code} />{/if}

  {#if loading}
    <div class="skeleton h-40 w-full"></div>
  {:else if note}
    <header class="flex flex-wrap items-start justify-between gap-3">
      <div class="min-w-0 flex-1">
        <input
          class="w-full border-0 bg-transparent p-0 text-xl font-semibold tracking-tight text-ink-50 focus:outline-none disabled:text-ink-50"
          bind:value={title}
          disabled={!writable}
          maxlength={500}
          aria-label="Note title"
        />
        <p class="mt-1 flex flex-wrap items-center gap-2 text-[11px] text-ink-500">
          <span>updated {relativeTime(note.updated_at)}</span>
          <span>·</span>
          <span>{note.chunk_count} passages indexed</span>
          {#if note.embedding_model}
            <span>·</span>
            <span class="font-mono">{note.embedding_model}</span>
          {/if}
          {#if writable}
            <span>·</span>
            <span
              class={cx(
                saveState === 'error'
                  ? 'text-red-400'
                  : saveState === 'saving' || saveState === 'dirty'
                    ? 'text-amber-400'
                    : 'text-emerald-400'
              )}
            >
              {SAVE_LABEL[saveState]}
            </span>
          {/if}
        </p>
      </div>
      <div class="flex shrink-0 items-center gap-2">
        <StatusPill status={note.status} stage={note.processing_stage} />
        {#if writable}
          <button class="btn-primary px-3 py-1.5 text-xs" onclick={saveNow} disabled={saveState === 'saving'}>
            Save
          </button>
          <button class="btn-ghost px-2 py-1.5 text-xs text-red-400" onclick={remove}>Delete</button>
        {/if}
      </div>
    </header>

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

    <!-- Tags and collection -->
    <div class="flex flex-wrap items-center gap-2">
      <ul class="flex flex-wrap gap-1.5">
        {#each tags as tag (tag)}
          <li class="group flex items-center gap-1 rounded-full bg-ink-800 px-2 py-0.5 text-[11px] text-ink-300">
            {tag}
            {#if writable}
              <button
                class="text-ink-600 hover:text-red-400"
                onclick={() => removeTag(tag)}
                aria-label="Remove tag {tag}">×</button
              >
            {/if}
          </li>
        {/each}
      </ul>
      {#if writable}
        <input
          class="w-28 rounded-full border border-ink-700 bg-transparent px-2 py-0.5 text-[11px] text-ink-200 placeholder:text-ink-600 focus:border-accent-500 focus:outline-none"
          placeholder="+ tag"
          bind:value={tagInput}
          onkeydown={(e) => {
            if (e.key === 'Enter') {
              e.preventDefault();
              void addTag();
            }
          }}
          aria-label="Add a tag"
        />
        {#if collections.length > 0}
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
      {/if}
    </div>

    <!-- Editor: source and preview side by side -->
    <div class="grid gap-4 lg:grid-cols-2">
      <div class="space-y-1.5">
        <div class="flex items-center justify-between">
          <p class="text-[11px] uppercase tracking-wide text-ink-500">Markdown</p>
          <p class="text-[10px] text-ink-600">
            {draftLinks.length} link{draftLinks.length === 1 ? '' : 's'} in this draft
          </p>
        </div>
        <textarea
          class="input min-h-[26rem] resize-y font-mono text-[13px] leading-relaxed"
          bind:value={body}
          oninput={(e) => onBodyInput((e.currentTarget as HTMLTextAreaElement).value)}
          onkeydown={(e) => {
            if ((e.metaKey || e.ctrlKey) && e.key === 's') {
              e.preventDefault();
              void saveNow();
            }
          }}
          disabled={!writable}
          spellcheck="false"
          aria-label="Note body, Markdown"
        ></textarea>
        <p class="text-[10px] text-ink-600">
          **bold** · *italic* · `code` · # heading · - list · &gt; quote · [[Note title]] to link
        </p>
      </div>

      <div class="space-y-1.5">
        <p class="text-[11px] uppercase tracking-wide text-ink-500">Preview</p>
        <div class="min-h-[26rem] rounded-lg border border-ink-800 bg-ink-900/40 px-4 py-3">
          {#if body.trim()}
            <MarkdownView source={body} wikiLinks={wikiMap} />
          {:else}
            <p class="text-xs text-ink-600">Nothing written yet.</p>
          {/if}
        </div>
      </div>
    </div>

    {#if draftLinks.some((t) => !savedTargets.has(t)) && saveState !== 'saved'}
      <p class="text-[11px] text-amber-400/80">
        New links resolve when you save.
      </p>
    {/if}

    <!-- Links, both directions -->
    <div class="grid gap-4 lg:grid-cols-2">
      <section class="card space-y-2 p-4">
        <div class="flex items-center justify-between">
          <h2 class="text-sm font-medium text-ink-200">Backlinks</h2>
          <span class="text-[11px] text-ink-500">{note.backlinks.length}</span>
        </div>
        {#if note.backlinks.length === 0}
          <p class="py-3 text-[11px] text-ink-500">
            No other note links here. Write <code class="text-accent-400">[[{note.title}]]</code> in
            another note to create one.
          </p>
        {:else}
          <ul class="space-y-1">
            {#each note.backlinks as link (link.id)}
              <li>
                <a
                  href="/notes/{link.id}"
                  class="flex items-center justify-between gap-2 rounded-lg px-2 py-1 text-[13px] text-ink-100 hover:bg-ink-800/60"
                >
                  <span class="truncate">{link.title}</span>
                  <span class="shrink-0 text-[10px] text-ink-500"
                    >{relativeTime(link.updated_at)}</span
                  >
                </a>
              </li>
            {/each}
          </ul>
        {/if}
      </section>

      <section class="card space-y-2 p-4">
        <div class="flex items-center justify-between">
          <h2 class="text-sm font-medium text-ink-200">Links from this note</h2>
          {#if unresolvedCount > 0}
            <span class="text-[11px] text-ink-500">{unresolvedCount} unwritten</span>
          {/if}
        </div>
        {#if note.outgoing_links.length === 0}
          <p class="py-3 text-[11px] text-ink-500">
            No outgoing links. Type <code class="text-accent-400">[[a note title]]</code> above.
          </p>
        {:else}
          <ul class="space-y-1">
            {#each note.outgoing_links as link (link.target_title)}
              <li class="flex items-center justify-between gap-2 px-2 py-1 text-[13px]">
                {#if link.resolved && link.target_id}
                  <a href="/notes/{link.target_id}" class="truncate text-ink-100 hover:text-accent-400"
                    >{link.target_title}</a
                  >
                  {#if link.status}
                    <StatusPill status={link.status} />
                  {/if}
                {:else}
                  <!-- Shown, not hidden: the user wrote this reference on purpose. -->
                  <span class="truncate text-ink-500" title="No note with this title yet"
                    >{link.target_title}</span
                  >
                  <span class="shrink-0 text-[10px] text-ink-600">not written</span>
                {/if}
              </li>
            {/each}
          </ul>
        {/if}
      </section>
    </div>

    {#if !writable}
      <p class="text-[11px] text-ink-500">
        You have read-only access to this workspace, so this note cannot be edited.
      </p>
    {/if}
  {:else}
    <p class="text-sm text-ink-400">This note does not exist in this workspace.</p>
  {/if}
</div>
