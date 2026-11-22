<script lang="ts">
  /**
   * Workspace switcher.
   *
   * Shows the active workspace and lets the user change it. Creating a new one
   * is inline because a SaaS-shaped app needs it from day one; joining someone
   * else's is done from Settings → Members, which is where the permissions are
   * explained.
   */
  import { goto } from '$app/navigation';
  import { page } from '$app/state';
  import {
    activeWorkspace,
    activeWorkspaceId,
    createWorkspace,
    selectWorkspace,
    workspaces,
    workspacesLoading
  } from '$lib/stores/workspace';
  import { NexusApiError } from '$lib/api/client';
  import { ROLE_LABEL } from '$lib/api/types';

  let open = $state(false);
  let creating = $state(false);
  let newName = $state('');
  let error = $state<string | null>(null);
  let busy = $state(false);

  async function choose(id: string) {
    if (id === $activeWorkspaceId) {
      open = false;
      return;
    }
    selectWorkspace(id);
    open = false;
    // Reload so every page refetches against the new tenant rather than showing
    // the previous workspace's rows until the next navigation.
    await goto(page.url.pathname, { invalidateAll: true });
  }

  async function submit() {
    const name = newName.trim();
    if (!name || busy) return;
    busy = true;
    error = null;
    try {
      await createWorkspace(name);
      newName = '';
      creating = false;
      open = false;
      await goto('/dashboard', { invalidateAll: true });
    } catch (err) {
      error =
        err instanceof NexusApiError ? err.message : 'Could not create the workspace.';
    } finally {
      busy = false;
    }
  }
</script>

<div class="relative">
  <button
    type="button"
    class="flex w-full items-center justify-between gap-2 rounded-lg border border-ink-800 px-2.5 py-2 text-left text-xs transition-colors hover:border-ink-700"
    onclick={() => (open = !open)}
    aria-haspopup="menu"
    aria-expanded={open}
  >
    <span class="min-w-0">
      <span class="block truncate font-medium text-ink-100">
        {#if $workspacesLoading}
          Loading…
        {:else}
          {$activeWorkspace?.name ?? 'No workspace'}
        {/if}
      </span>
      {#if $activeWorkspace}
        <span class="block text-[10px] uppercase tracking-wide text-ink-500">
          {ROLE_LABEL[$activeWorkspace.role]}
          {#if $activeWorkspace.member_count > 1}
            · {$activeWorkspace.member_count} members
          {/if}
        </span>
      {/if}
    </span>
    <span class="text-ink-500">{open ? '▴' : '▾'}</span>
  </button>

  {#if open}
    <div
      class="absolute left-0 right-0 z-30 mt-1 rounded-lg border border-ink-700 bg-ink-850 p-1 shadow-xl"
      role="menu"
    >
      {#each $workspaces as workspace (workspace.id)}
        <button
          type="button"
          class="flex w-full items-center justify-between gap-2 rounded px-2 py-1.5 text-left text-xs hover:bg-ink-800"
          class:bg-ink-800={workspace.id === $activeWorkspaceId}
          onclick={() => choose(workspace.id)}
          role="menuitem"
        >
          <span class="min-w-0 truncate text-ink-200">{workspace.name}</span>
          <span class="shrink-0 text-[10px] text-ink-500">{ROLE_LABEL[workspace.role]}</span>
        </button>
      {/each}

      <div class="my-1 border-t border-ink-800"></div>

      {#if creating}
        <form
          class="space-y-1 p-1"
          onsubmit={(e) => {
            e.preventDefault();
            void submit();
          }}
        >
          <input
            class="input text-xs"
            placeholder="Workspace name"
            bind:value={newName}
            maxlength={120}
            required
          />
          <div class="flex gap-1">
            <button class="btn-primary flex-1 text-xs" type="submit" disabled={busy}>
              Create
            </button>
            <button
              class="btn-ghost text-xs"
              type="button"
              onclick={() => {
                creating = false;
                newName = '';
              }}
            >
              Cancel
            </button>
          </div>
        </form>
      {:else}
        <button
          type="button"
          class="w-full rounded px-2 py-1.5 text-left text-xs text-ink-300 hover:bg-ink-800"
          onclick={() => (creating = true)}
        >
          + New workspace
        </button>
      {/if}

      {#if error}
        <p class="px-2 py-1 text-[10px] text-red-400">{error}</p>
      {/if}
    </div>
  {/if}
</div>
