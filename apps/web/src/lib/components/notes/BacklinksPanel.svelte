<script lang="ts">
  /**
   * Both directions of a note's links, side by side.
   *
   * They are one component because they are the same idea seen from two sides,
   * and splitting them would duplicate the rule that matters: **an unresolved
   * link is shown, not hidden.** A `[[Q3 Budget]]` pointing at a note that does
   * not exist is a promise the author made; rendering it as an unclickable
   * "not written" is honest, while dropping it would make the panel quietly
   * disagree with the note body.
   */
  import type { NoteBacklink, NoteOutgoingLink } from '$lib/api/types';
  import ProcessingStatus from '$lib/components/documents/ProcessingStatus.svelte';
  import { relativeTime } from '$lib/utils/format';

  let {
    noteTitle,
    backlinks = [],
    outgoing = []
  }: {
    noteTitle: string;
    backlinks?: NoteBacklink[];
    outgoing?: NoteOutgoingLink[];
  } = $props();

  const unwritten = $derived(outgoing.filter((link) => !link.resolved).length);
</script>

<div class="grid gap-4 lg:grid-cols-2">
  <section class="card space-y-2 p-4">
    <div class="flex items-center justify-between">
      <h2 class="text-sm font-medium text-ink-200">Backlinks</h2>
      <span class="text-[11px] text-ink-500">{backlinks.length}</span>
    </div>
    {#if backlinks.length === 0}
      <p class="py-3 text-[11px] text-ink-500">
        No other note links here. Write
        <code class="text-accent-400">[[{noteTitle}]]</code> in another note to create one.
      </p>
    {:else}
      <ul class="space-y-1">
        {#each backlinks as link (link.id)}
          <li>
            <a
              href="/notes/{link.id}"
              class="flex items-center justify-between gap-2 rounded-lg px-2 py-1 text-[13px] text-ink-100 hover:bg-ink-800/60"
            >
              <span class="truncate">{link.title}</span>
              <span class="shrink-0 text-[10px] text-ink-500">{relativeTime(link.updated_at)}</span>
            </a>
          </li>
        {/each}
      </ul>
    {/if}
  </section>

  <section class="card space-y-2 p-4">
    <div class="flex items-center justify-between">
      <h2 class="text-sm font-medium text-ink-200">Links from this note</h2>
      {#if unwritten > 0}
        <span class="text-[11px] text-ink-500">{unwritten} unwritten</span>
      {/if}
    </div>
    {#if outgoing.length === 0}
      <p class="py-3 text-[11px] text-ink-500">
        No outgoing links. Type <code class="text-accent-400">[[a note title]]</code> in the body.
      </p>
    {:else}
      <ul class="space-y-1">
        {#each outgoing as link (link.target_title)}
          <li class="flex items-center justify-between gap-2 px-2 py-1 text-[13px]">
            {#if link.resolved && link.target_id}
              <a href="/notes/{link.target_id}" class="truncate text-ink-100 hover:text-accent-400"
                >{link.target_title}</a
              >
              {#if link.status}
                <ProcessingStatus status={link.status} compact />
              {/if}
            {:else}
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
