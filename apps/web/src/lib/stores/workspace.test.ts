import { get } from 'svelte/store';
import { beforeEach, describe, expect, it, vi } from 'vitest';

/**
 * A failed workspace load must leave a visible trace.
 *
 * The bug this pins: `loadWorkspaces()` caught the API error, set the list to
 * `[]`, and returned. An empty list is indistinguishable from "a brand-new
 * account with no workspace", so pages that begin with
 * `if (!$activeWorkspaceId) return` never cleared their loading flag. The
 * deployed app, with no reachable API, showed a permanent spinner and no error
 * at all — the user could not tell a broken deployment from a slow one.
 *
 * `$lib/api/client` is mocked wholesale. The real module imports
 * `$env/dynamic/public`, which only resolves inside SvelteKit's transform, and
 * `NexusApiError` is re-exported from the mock so the store's `instanceof`
 * check still behaves exactly as it does in the app.
 */

class FakeNexusApiError extends Error {
  code: string;
  status: number;
  details: Record<string, unknown>;

  constructor(status: number, code: string, message: string, details: Record<string, unknown> = {}) {
    super(message);
    this.name = 'NexusApiError';
    this.code = code;
    this.status = status;
    this.details = details;
  }
}

const apiFetch = vi.fn();

vi.mock('$lib/api/client', () => ({
  apiFetch: (...args: unknown[]) => apiFetch(...args),
  NexusApiError: FakeNexusApiError
}));

const { loadWorkspaces, workspaces, activeWorkspaceId, workspacesError, workspacesLoading } =
  await import('./workspace');

beforeEach(() => {
  apiFetch.mockReset();
  workspaces.set([]);
  activeWorkspaceId.set(null);
  workspacesError.set(null);
  workspacesLoading.set(true);
  // The environment is `node` (see vitest.config.ts), so `localStorage` does not
  // exist. The store guards every access with `typeof window === 'undefined'`,
  // which is the production behaviour on the server; nothing here needs it.
});

describe('loadWorkspaces', () => {
  it('records the error instead of silently discarding it', async () => {
    apiFetch.mockRejectedValue(
      new FakeNexusApiError(502, 'api_unreachable', 'The API could not be reached.')
    );

    await loadWorkspaces();

    const reported = get(workspacesError);
    expect(reported).not.toBeNull();
    expect(reported?.code).toBe('api_unreachable');
    expect(reported?.message).toContain('could not be reached');
  });

  it('stops the spinner even when the load fails', async () => {
    // The unkillable-spinner bug in one assertion: loading must return to false
    // on the failure path, not only on the success path.
    apiFetch.mockRejectedValue(new FakeNexusApiError(502, 'api_unreachable', 'down'));

    await loadWorkspaces();

    expect(get(workspacesLoading)).toBe(false);
  });

  it('clears a stale active workspace so pages do not build paths from it', async () => {
    activeWorkspaceId.set('stale-workspace-id');
    apiFetch.mockRejectedValue(new FakeNexusApiError(502, 'api_unreachable', 'down'));

    await loadWorkspaces();

    expect(get(activeWorkspaceId)).toBeNull();
  });

  it('clears a previous error on a successful retry', async () => {
    apiFetch.mockRejectedValueOnce(new FakeNexusApiError(502, 'api_unreachable', 'down'));
    await loadWorkspaces();
    expect(get(workspacesError)).not.toBeNull();

    apiFetch.mockResolvedValueOnce([
      { id: 'ws-1', name: 'Personal', slug: 'personal-abc123', personal: true, role: 'owner' }
    ]);
    await loadWorkspaces();

    expect(get(workspacesError)).toBeNull();
    expect(get(activeWorkspaceId)).toBe('ws-1');
    expect(get(workspacesLoading)).toBe(false);
  });

  it('does not treat an empty workspace list as an error', async () => {
    // A genuinely new account can have no workspace. That is not a failure and
    // must not render an API error; the dashboard has a separate message for it.
    apiFetch.mockResolvedValueOnce([]);

    await loadWorkspaces();

    expect(get(workspacesError)).toBeNull();
    expect(get(activeWorkspaceId)).toBeNull();
    expect(get(workspacesLoading)).toBe(false);
  });
});
