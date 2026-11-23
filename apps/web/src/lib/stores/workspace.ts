/**
 * Workspace store: which workspace the app is currently scoped to.
 *
 * There is exactly one source of truth for the active workspace, and every API
 * path is built from it (see `wsPath`). That is the frontend half of tenancy:
 * a page cannot accidentally request another tenant's data, because it has no
 * other id to put in the URL.
 *
 * The active workspace is remembered in localStorage by id, not by object, so a
 * rename or a role change is picked up on the next load instead of being served
 * stale from the cache.
 */
import { writable, derived, get } from 'svelte/store';
import { apiFetch, NexusApiError } from '$lib/api/client';
import type { Workspace } from '$lib/api/types';

const STORAGE_KEY = 'nexus.activeWorkspaceId';

export const workspaces = writable<Workspace[]>([]);
export const activeWorkspaceId = writable<string | null>(null);
export const workspacesLoading = writable(true);

/**
 * Why the workspace list could not be loaded, or null.
 *
 * This exists because the previous behaviour swallowed the error into an empty
 * list, and an empty list is indistinguishable from "this account has no
 * workspace yet". Pages that open with `if (!$activeWorkspaceId) return` then
 * never clear their loading flag, so the user gets a spinner that never stops
 * and no explanation — which is exactly what a deployed build with no reachable
 * API produced. The failure has to be visible somewhere the UI can read it.
 */
export const workspacesError = writable<{ message: string; code?: string } | null>(null);

export const activeWorkspace = derived(
  [workspaces, activeWorkspaceId],
  ([$workspaces, $id]) => $workspaces.find((w) => w.id === $id) ?? null
);

function remember(id: string | null): void {
  if (typeof window === 'undefined') return;
  if (id) localStorage.setItem(STORAGE_KEY, id);
  else localStorage.removeItem(STORAGE_KEY);
}

function remembered(): string | null {
  if (typeof window === 'undefined') return null;
  return localStorage.getItem(STORAGE_KEY);
}

/** Prefer the stored workspace, then the personal one, then anything. */
function choose(list: Workspace[], preferred: string | null): string | null {
  if (preferred && list.some((w) => w.id === preferred)) return preferred;
  return (list.find((w) => w.personal) ?? list[0])?.id ?? null;
}

export async function loadWorkspaces(): Promise<void> {
  workspacesLoading.set(true);
  workspacesError.set(null);
  try {
    const list = await apiFetch<Workspace[]>('/workspaces');
    workspaces.set(list);
    const next = choose(list, get(activeWorkspaceId) ?? remembered());
    activeWorkspaceId.set(next);
    remember(next);
  } catch (err) {
    // A failed workspace load is not fatal to the shell, but it must leave a
    // trace. Previously it did not, and the result was an unkillable spinner.
    if (!(err instanceof NexusApiError)) throw err;
    workspaces.set([]);
    activeWorkspaceId.set(null);
    workspacesError.set({ message: err.message, code: err.code });
  } finally {
    workspacesLoading.set(false);
  }
}

export function selectWorkspace(id: string): void {
  activeWorkspaceId.set(id);
  remember(id);
}

export async function createWorkspace(name: string): Promise<Workspace> {
  const created = await apiFetch<Workspace>('/workspaces', {
    method: 'POST',
    body: { name }
  });
  workspaces.update((list) => [...list, created]);
  selectWorkspace(created.id);
  return created;
}

export async function renameWorkspace(id: string, name: string): Promise<void> {
  const updated = await apiFetch<Workspace>(`/workspaces/${id}`, {
    method: 'PATCH',
    body: { name }
  });
  workspaces.update((list) => list.map((w) => (w.id === id ? updated : w)));
}

export async function deleteWorkspace(id: string): Promise<void> {
  await apiFetch<void>(`/workspaces/${id}`, { method: 'DELETE' });
  workspaces.update((list) => list.filter((w) => w.id !== id));
  if (get(activeWorkspaceId) === id) {
    const next = choose(get(workspaces), null);
    activeWorkspaceId.set(next);
    remember(next);
  }
}

/**
 * Build a workspace-scoped API path.
 *
 * Throws rather than returning a path with an empty segment: a request to
 * `/workspaces//notes` would 404 confusingly, and a request with a *wrong*
 * id would be worse. Failing loudly here is the safe default.
 */
export function wsPath(suffix: string): string {
  const id = get(activeWorkspaceId);
  if (!id) throw new NexusApiError(0, 'no_workspace', 'No workspace is selected.');
  return `/workspaces/${id}${suffix}`;
}
