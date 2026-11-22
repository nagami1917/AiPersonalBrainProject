<script lang="ts">
  /**
   * Members panel.
   *
   * The role copy is the important part: a "viewer" is a promise the database
   * actually keeps (RLS refuses writes), so the UI states what each role can do
   * instead of offering controls that would only fail later.
   */
  import { onMount } from 'svelte';
  import { apiFetch, NexusApiError } from '$lib/api/client';
  import type { WorkspaceMember, WorkspaceRole } from '$lib/api/types';
  import { ROLE_LABEL, canManage } from '$lib/api/types';
  import {
    activeWorkspace,
    activeWorkspaceId,
    loadWorkspaces
  } from '$lib/stores/workspace';
  import ErrorNotice from '$lib/components/ErrorNotice.svelte';

  const ROLE_HINT: Record<WorkspaceRole, string> = {
    owner: 'Full control, including deleting the workspace',
    admin: 'Manage members and all content',
    member: 'Create and edit content',
    viewer: 'Read-only access to everything in the workspace'
  };

  let members = $state<WorkspaceMember[]>([]);
  let loading = $state(true);
  let error = $state<{ message: string; code?: string } | null>(null);
  let email = $state('');
  let role = $state<'admin' | 'member' | 'viewer'>('member');
  let adding = $state(false);
  let notice = $state<string | null>(null);

  const manage = $derived(canManage($activeWorkspace?.role));
  const isOwner = $derived($activeWorkspace?.role === 'owner');

  async function load() {
    if (!$activeWorkspaceId) return;
    loading = true;
    try {
      members = await apiFetch<WorkspaceMember[]>(
        `/v1/workspaces/${$activeWorkspaceId}/members`
      );
    } catch (err) {
      error =
        err instanceof NexusApiError
          ? { message: err.message, code: err.code }
          : { message: 'Could not load members.' };
    } finally {
      loading = false;
    }
  }

  onMount(load);

  // Re-load whenever the active workspace changes: a member list is per-tenant.
  $effect(() => {
    if ($activeWorkspaceId) void load();
  });

  async function addMember() {
    if (!email.trim() || !$activeWorkspaceId) return;
    adding = true;
    error = null;
    notice = null;
    try {
      await apiFetch(`/v1/workspaces/${$activeWorkspaceId}/members`, {
        method: 'POST',
        body: { email: email.trim(), role }
      });
      email = '';
      notice = 'Added. They will see this workspace the next time they load NEXUS.';
      await load();
      await loadWorkspaces();
    } catch (err) {
      error =
        err instanceof NexusApiError
          ? { message: err.message, code: err.code }
          : { message: 'Could not add that person.' };
    } finally {
      adding = false;
    }
  }

  async function changeRole(userId: string, next: 'admin' | 'member' | 'viewer') {
    if (!$activeWorkspaceId) return;
    error = null;
    try {
      await apiFetch(`/v1/workspaces/${$activeWorkspaceId}/members/${userId}`, {
        method: 'PATCH',
        body: { role: next }
      });
      await load();
    } catch (err) {
      error =
        err instanceof NexusApiError
          ? { message: err.message, code: err.code }
          : { message: 'Could not change that role.' };
    }
  }

  async function remove(userId: string) {
    if (!$activeWorkspaceId) return;
    error = null;
    try {
      await apiFetch(`/v1/workspaces/${$activeWorkspaceId}/members/${userId}`, {
        method: 'DELETE'
      });
      await load();
    } catch (err) {
      error =
        err instanceof NexusApiError
          ? { message: err.message, code: err.code }
          : { message: 'Could not remove that member.' };
    }
  }
</script>

<section class="card divide-y divide-ink-800">
  <div class="px-4 py-3">
    <h2 class="text-xs font-medium uppercase tracking-wide text-ink-500">Members</h2>
    <p class="mt-1 text-[11px] text-ink-500">
      Everyone here shares this workspace's knowledge. Access is enforced by the database, not by
      hiding buttons.
    </p>
  </div>

  {#if error}
    <div class="px-4 py-3"><ErrorNotice message={error.message} code={error.code} /></div>
  {/if}

  <ul class="divide-y divide-ink-800">
    {#each members as member (member.user_id)}
      <li class="flex items-center justify-between gap-3 px-4 py-2.5">
        <div class="min-w-0">
          <p class="truncate text-sm text-ink-200">
            {member.display_name ?? member.user_id.slice(0, 8)}
            {#if member.user_id === $activeWorkspace?.owner_id}
              <span class="ml-1 text-[10px] uppercase tracking-wide text-accent-500">owner</span>
            {/if}
          </p>
          <p class="text-[11px] text-ink-500">{ROLE_HINT[member.role]}</p>
        </div>

        {#if manage && member.user_id !== $activeWorkspace?.owner_id}
          <div class="flex shrink-0 items-center gap-1.5">
            <select
              class="input w-auto py-1 text-xs"
              value={member.role}
              onchange={(e) =>
                changeRole(member.user_id, e.currentTarget.value as 'admin' | 'member' | 'viewer')}
            >
              <option value="admin">Admin</option>
              <option value="member">Member</option>
              <option value="viewer">Viewer</option>
            </select>
            <button class="btn-ghost text-xs" onclick={() => remove(member.user_id)}>
              Remove
            </button>
          </div>
        {:else}
          <span class="shrink-0 text-xs text-ink-500">{ROLE_LABEL[member.role]}</span>
        {/if}
      </li>
    {/each}
    {#if loading && members.length === 0}
      <li class="px-4 py-3 text-xs text-ink-500">Loading members…</li>
    {/if}
  </ul>

  {#if manage}
    <form
      class="space-y-2 px-4 py-3"
      onsubmit={(e) => {
        e.preventDefault();
        void addMember();
      }}
    >
      <label class="label" for="member-email">Add someone who already has a NEXUS account</label>
      <div class="flex flex-wrap gap-2">
        <input
          id="member-email"
          class="input min-w-40 flex-1"
          type="email"
          placeholder="name@example.com"
          bind:value={email}
          required
        />
        <select class="input w-auto" bind:value={role}>
          <option value="viewer">Viewer</option>
          <option value="member">Member</option>
          <option value="admin">Admin</option>
        </select>
        <button class="btn-primary shrink-0" type="submit" disabled={adding}>
          {adding ? 'Adding…' : 'Add'}
        </button>
      </div>
      <p class="text-[11px] text-ink-500">
        Invitations by email are not implemented; this adds an existing account by address.
      </p>
      {#if notice}<p class="text-[11px] text-accent-400">{notice}</p>{/if}
    </form>
  {/if}

  {#if isOwner && !$activeWorkspace?.personal}
    <div class="px-4 py-3">
      <p class="text-[11px] text-ink-500">
        Deleting a workspace deletes everything inside it. It is available in the workspace
        switcher menu for non-personal workspaces.
      </p>
    </div>
  {/if}
</section>
