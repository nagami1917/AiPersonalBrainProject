<script lang="ts">
  /**
   * Note detail.
   *
   * Notes and documents are separate tables, so a note has its own page: the
   * route is what makes a citation link honest (`/notes/…` vs `/documents/…`).
   */
  import { page } from '$app/state';
  import { onMount } from 'svelte';
  import { apiFetch, NexusApiError } from '$lib/api/client';
  import type { NoteDetail } from '$lib/api/types';
  import { canWrite } from '$lib/api/types';
  import { wsPath, activeWorkspace, activeWorkspaceId } from '$lib/stores/workspace';
  import StatusPill from '$lib/components/StatusPill.svelte';
  import ErrorNotice from '$lib/components/ErrorNotice.svelte';
  import { relativeTime } from '$lib/utils/format';

  let note = $state<NoteDetail | null>(null);
  let loading = $state(true);
  let error = $state<{ message: string; code?: string } | null>(null);
  let editing = $state(false);
  let title = $state('');
  let body = $state('');
  let saving = $state(false);

  const writable = $derived(canWrite($activeWorkspace?.role));
  const id = $derived(page.params.id);

  async function load() {
    if (!id || !$activeWorkspaceId) return;
    loading = true;
    error = null;
    try {
      note = await apiFetch<NoteDetail>(wsPath(`/notes/${id}`));
      title = note.title;
      body = note.content ?? '';
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

  async function save() {
    if (!note || saving) return;
    saving = true;
    error = null;
    try {
      await apiFetch(wsPath(`/notes/${note.id}`), {
        method: 'PATCH',
        body: { title: title.trim(), content: body }
      });
      editing = false;
      await load();
    } catch (err) {
      error =
        err instanceof NexusApiError
          ? { message: err.message, code: err.code }
          : { message: 'Could not save the note.' };
    } finally {
      saving = false;
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
</script>

<svelte:head><title>{note?.title ?? 'Note'} · NEXUS</title></svelte:head>

<div class="space-y-5">
  <a href="/notes" class="text-xs text-ink-500 hover:text-ink-300">← Notes</a>

  {#if error}<ErrorNotice message={error.message} code={error.code} />{/if}

  {#if loading}
    <div class="skeleton h-40 w-full"></div>
  {:else if note}
    <header class="flex flex-wrap items-start justify-between gap-3">
      <div class="min-w-0">
        {#if editing}
          <input class="input text-lg" bind:value={title} maxlength={500} />
        {:else}
          <h1 class="text-xl font-semibold tracking-tight text-ink-50">{note.title}</h1>
        {/if}
        <p class="mt-1 flex flex-wrap items-center gap-2 text-[11px] text-ink-500">
          <span>updated {relativeTime(note.updated_at)}</span>
          <span>·</span>
          <span>{note.chunk_count} passages indexed</span>
          {#if note.embedding_model}
            <span>·</span>
            <span class="font-mono">{note.embedding_model}</span>
          {/if}
        </p>
      </div>
      <div class="flex shrink-0 items-center gap-2">
        <StatusPill status={note.status} stage={note.processing_stage} />
        {#if writable}
          {#if editing}
            <button class="btn-primary" onclick={save} disabled={saving}>
              {saving ? 'Saving…' : 'Save'}
            </button>
            <button class="btn-ghost" onclick={() => (editing = false)}>Cancel</button>
          {:else}
            <button class="btn-ghost" onclick={() => (editing = true)}>Edit</button>
            <button class="btn-ghost text-red-400" onclick={remove}>Delete</button>
          {/if}
        {/if}
      </div>
    </header>

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

    {#if note.tags.length > 0}
      <ul class="flex flex-wrap gap-1.5">
        {#each note.tags as tag}
          <li class="rounded-full bg-ink-800 px-2 py-0.5 text-[11px] text-ink-300">{tag}</li>
        {/each}
      </ul>
    {/if}

    {#if editing}
      <textarea class="input min-h-72 font-mono text-[13px]" bind:value={body}></textarea>
    {:else}
      <article class="prose-nexus space-y-2 text-sm leading-relaxed text-ink-200">
        {#each (note.content ?? '').split('\n\n') as paragraph}
          <p>{paragraph}</p>
        {/each}
      </article>
    {/if}

    {#if !writable}
      <p class="text-[11px] text-ink-500">
        You have read-only access to this workspace, so this note cannot be edited.
      </p>
    {/if}
  {:else}
    <p class="text-sm text-ink-400">This note does not exist in this workspace.</p>
  {/if}
</div>
