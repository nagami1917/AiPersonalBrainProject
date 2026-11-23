<!--
  The trash: what was deleted, and how long it has left.

  ## Why this is a component and not two pages

  Notes and documents share a deletion lifecycle and a restore endpoint shape,
  so the panel is driven by `kind`. The alternative — a trash page per type —
  duplicates the countdown, the restore button and the empty state twice, and
  the two copies drift.

  ## Why the expiry is shown at all

  A retention window the user cannot see is a trap: deletion feels permanent
  ("why keep something I cannot find?") or recoverable forever ("I'll undelete
  it next month"), and both are wrong. Showing days left converts the window
  into a deadline the reader can act on.

  ## Why restore is optimistic-free

  The row is removed from this list only after the server confirms. A restore
  that quietly failed and left an item apparently recovered is the one failure
  mode that loses data: the user stops worrying about a note that is still
  scheduled for deletion.
-->
<script lang="ts">
  import { apiFetch, NexusApiError } from '$lib/api/client';
  import { wsPath } from '$lib/stores/workspace';
  import { relativeTime } from '$lib/utils/format';

  type TrashItem = {
    id: string;
    kind: 'note' | 'document';
    title: string;
    snippet?: string;
    filename?: string | null;
    mime_type?: string | null;
    deleted_at: string;
    expires_at: string;
  };

  type TrashResponse = {
    items: TrashItem[];
    retention_days: number;
  };

  let { kind, onrestored, onload }: {
    kind: 'notes' | 'documents';
    /** Called after a successful restore so the parent can refresh its list. */
    onrestored?: (id: string) => void;
    /** Called whenever the trash is (re)loaded, so a parent can show a count. */
    onload?: (count: number) => void;
  } = $props();

  let items = $state<TrashItem[]>([]);
  let retentionDays = $state(30);
  let loading = $state(true);
  let error = $state<string | null>(null);
  let restoring = $state<string | null>(null);

  async function load() {
    loading = true;
    error = null;
    try {
      const response = await apiFetch<TrashResponse>(wsPath(`/${kind}/trash`));
      items = response.items;
      retentionDays = response.retention_days;
      onload?.(response.items.length);
    } catch (err) {
      error = err instanceof NexusApiError ? err.message : 'Could not load the trash.';
    } finally {
      loading = false;
    }
  }

  async function restore(item: TrashItem) {
    restoring = item.id;
    error = null;
    try {
      await apiFetch(wsPath(`/${kind}/${item.id}/restore`), { method: 'POST' });
      items = items.filter((entry) => entry.id !== item.id);
      onrestored?.(item.id);
    } catch (err) {
      error = err instanceof NexusApiError ? err.message : 'Could not restore that item.';
    } finally {
      restoring = null;
    }
  }

  /** Whole days remaining, floored at 0 — never negative, never "-1 days left". */
  function daysLeft(expiresAt: string): number {
    const ms = new Date(expiresAt).getTime() - Date.now();
    return Math.max(0, Math.floor(ms / 86_400_000));
  }

  $effect(() => {
    void load();
  });
</script>

<div class="space-y-3">
  <div class="rounded-lg border border-ink-800 bg-ink-900/40 px-3 py-2 text-[11px] text-ink-500">
    Deleted items stay here for {retentionDays} {retentionDays === 1 ? 'day' : 'days'} and are
    then removed permanently.
  </div>

  {#if error}
    <p class="rounded border border-red-900/60 bg-red-950/30 px-3 py-2 text-[11px] text-red-300">
      {error}
    </p>
  {/if}

  {#if loading}
    <div class="space-y-2">
      {#each [1, 2] as i}<div class="skeleton h-14 w-full"></div>{/each}
    </div>
  {:else if items.length === 0}
    <p class="rounded-xl border border-dashed border-ink-800 px-4 py-8 text-center text-xs text-ink-500">
      The trash is empty.
    </p>
  {:else}
    <ul class="divide-y divide-ink-800 rounded-xl border border-ink-800">
      {#each items as item (item.id)}
        <li class="flex items-start justify-between gap-4 px-4 py-3">
          <div class="min-w-0">
            <p class="truncate text-sm text-ink-200">{item.title}</p>
            {#if item.filename}
              <p class="truncate text-[11px] text-ink-600">{item.filename}</p>
            {:else if item.snippet}
              <p class="truncate text-[11px] text-ink-600">{item.snippet}</p>
            {/if}
            <p class="text-[11px] text-ink-500">
              deleted {relativeTime(item.deleted_at)} ·
              {#if daysLeft(item.expires_at) === 0}
                <span class="text-amber-400">removed soon</span>
              {:else}
                {daysLeft(item.expires_at)} days left
              {/if}
            </p>
          </div>
          <button
            type="button"
            class="btn-secondary shrink-0"
            onclick={() => void restore(item)}
            disabled={restoring === item.id}
          >
            {restoring === item.id ? 'Restoring…' : 'Restore'}
          </button>
        </li>
      {/each}
    </ul>
  {/if}
</div>
