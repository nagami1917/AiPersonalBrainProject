<script lang="ts">
  /**
   * The greeting block, and the workspace's front door.
   *
   * Two states, and the difference between them is the whole point:
   *
   *  - **populated** — a short line of orientation and the two actions worth
   *    taking from here.
   *  - **empty** — the page replaces its stat cards with this card alone. Four
   *    cards reading zero is not information; it is the absence of it, repeated.
   */
  import { activeWorkspace } from '$lib/stores/workspace';

  let {
    empty = false,
    noteCount = 0,
    documentCount = 0
  }: { empty?: boolean; noteCount?: number; documentCount?: number } = $props();

  const greeting = $derived.by(() => {
    const hour = new Date().getHours();
    if (hour < 5) return 'Still up';
    if (hour < 12) return 'Good morning';
    if (hour < 18) return 'Good afternoon';
    return 'Good evening';
  });
</script>

<section class="space-y-4">
  <div class="flex flex-wrap items-end justify-between gap-3">
    <div class="min-w-0">
      <h1 class="text-2xl font-semibold tracking-tight text-ink-50">
        {greeting}{#if $activeWorkspace} in {$activeWorkspace.name}{/if}
      </h1>
      <p class="mt-1 text-sm text-ink-400">
        {#if empty}
          Your knowledge, connected — once there is something to connect.
        {:else}
          {noteCount} {noteCount === 1 ? 'note' : 'notes'} and
          {documentCount} {documentCount === 1 ? 'document' : 'documents'}, all searchable and
          citable.
        {/if}
      </p>
    </div>

    <div class="flex shrink-0 items-center gap-2">
      <a class="btn-ghost text-xs" href="/search">Search</a>
      <a class="btn-primary text-xs" href="/assistant">Ask the assistant</a>
    </div>
  </div>

  {#if empty}
    <!-- One clear next step. The two halves of "add knowledge" are equal, so
         they get equal weight rather than a primary and a barely-visible link. -->
    <div class="card px-6 py-10 text-center">
      <h2 class="text-sm font-medium text-ink-100">This workspace is empty</h2>
      <p class="mx-auto mt-1 max-w-md text-sm text-ink-400">
        Add a document or write a note and NEXUS starts building your knowledge graph. Answers
        cite what you add, so nothing works until something is here.
      </p>
      <div class="mt-5 flex flex-wrap justify-center gap-2">
        <a class="btn-primary" href="/documents">Upload a document</a>
        <a class="btn-ghost" href="/notes">Write a note</a>
      </div>
    </div>
  {/if}
</section>
