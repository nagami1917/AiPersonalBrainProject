<script lang="ts">
  /**
   * Tags on a note, add and remove.
   *
   * A tag is sent as the whole list, not a delta, because the API's PATCH
   * replaces the set — sending "add X" would need a second verb the API does
   * not have.
   *
   * Names are lowercased and trimmed on the way in, matching the server's
   * uniqueness rule, so "Design" and "design " cannot become two tags that look
   * identical in the UI.
   */
  import { apiFetch, NexusApiError } from '$lib/api/client';
  import { wsPath } from '$lib/stores/workspace';

  let {
    noteId,
    tags = $bindable([]),
    editable = true,
    onerror
  }: {
    noteId: string;
    tags?: string[];
    editable?: boolean;
    onerror?: (message: string) => void;
  } = $props();

  let input = $state('');
  let busy = $state(false);

  async function persist(next: string[]) {
    busy = true;
    try {
      await apiFetch(wsPath(`/notes/${noteId}`), { method: 'PATCH', body: { tags: next } });
      tags = next;
      input = '';
    } catch (err) {
      onerror?.(
        err instanceof NexusApiError ? err.message : 'Could not update the tags on this note.'
      );
    } finally {
      busy = false;
    }
  }

  function add() {
    const name = input.trim().toLowerCase().replace(/\s+/g, ' ');
    if (!name || busy || tags.includes(name)) return;
    void persist([...tags, name]);
  }

  function remove(name: string) {
    if (busy) return;
    void persist(tags.filter((tag) => tag !== name));
  }
</script>

<div class="flex flex-wrap items-center gap-2">
  <ul class="flex flex-wrap gap-1.5">
    {#each tags as tag (tag)}
      <li
        class="flex items-center gap-1 rounded-full bg-ink-800 px-2 py-0.5 text-[11px] text-ink-300"
      >
        {tag}
        {#if editable}
          <button
            class="text-ink-600 hover:text-red-400"
            onclick={() => remove(tag)}
            disabled={busy}
            aria-label="Remove tag {tag}">×</button
          >
        {/if}
      </li>
    {/each}
  </ul>

  {#if editable}
    <input
      class="w-28 rounded-full border border-ink-700 bg-transparent px-2 py-0.5 text-[11px] text-ink-200 placeholder:text-ink-600 focus:border-accent-500 focus:outline-none"
      placeholder="+ tag"
      bind:value={input}
      disabled={busy}
      onkeydown={(e) => {
        if (e.key === 'Enter') {
          e.preventDefault();
          add();
        }
      }}
      aria-label="Add a tag"
    />
  {/if}
</div>
