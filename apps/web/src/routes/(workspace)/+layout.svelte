<script lang="ts">
  /**
   * The workspace shell: session gate, workspace list, and the two-column frame.
   *
   * It owns nothing visual beyond the frame — navigation lives in AppSidebar and
   * the page header lives in Topbar, so each can change without touching the
   * auth gate this file exists for.
   *
   * ## Why there is a gate rather than a redirect and nothing else
   *
   * Session restoration is asynchronous: Supabase reads the token from storage,
   * and until that resolves `$session` is null for a signed-in user. Redirecting
   * on `!$session` alone would bounce every reload to /login. Waiting for
   * `sessionReady` fixes that but introduces a third state — "we do not know
   * yet" — and rendering the shell during it shows an empty sidebar and pages
   * that fetch with no workspace, which reads as broken rather than loading.
   *
   * So there are three states, each rendered deliberately:
   *
   *   not ready   → a neutral restore screen (no navigation, no fetch)
   *   ready, none → redirect to /login
   *   ready, user → the shell
   *
   * This is UX only. FastAPI re-verifies the token on every request, so a
   * tampered client state grants nothing.
   */
  import { goto } from '$app/navigation';
  import { session, sessionReady } from '$lib/stores/session';
  import {
    loadWorkspaces,
    activeWorkspaceId,
    workspacesLoading,
    workspacesError
  } from '$lib/stores/workspace';
  import AppSidebar from '$lib/components/layout/AppSidebar.svelte';
  import ErrorNotice from '$lib/components/ErrorNotice.svelte';

  /**
   * Whether a page may render.
   *
   * Every page in this group builds its request paths with `wsPath()`, which
   * throws while no workspace is active. Rendering children before the
   * workspace list has resolved therefore produces a distracting page-level
   * error — "No workspace is selected." — on first paint, from code that is
   * working correctly and is about to succeed.
   *
   * Worse, when the list genuinely fails (no reachable API) that per-page error
   * is the *only* thing shown, once per page, and it points at the workspace
   * selector rather than at the actual cause.
   *
   * So the shell withholds pages until there is an active workspace, and states
   * the real reason when there is not. This is the one place every page is
   * guaranteed to pass through; fixing it per page was tried and left the
   * documents page still broken.
   */
  const blocked = $derived(
    !$workspacesLoading && !$workspacesError && !$activeWorkspaceId ? 'no_workspace' : null
  );
  const canRenderPage = $derived(!!$activeWorkspaceId);

  let { children } = $props();

  // Client-side gate: FastAPI re-verifies every request, so this is UX only,
  // never the security boundary.
  $effect(() => {
    if ($sessionReady && !$session) {
      goto('/login');
    }
  });

  // The workspace list is loaded once per session, not per page: every page then
  // builds its API paths from the same active workspace.
  $effect(() => {
    if ($session) void loadWorkspaces();
  });
</script>

{#if !$sessionReady}
  <!--
    The restore gate. Deliberately static: no sidebar, because a navigation
    shell with an empty workspace selector suggests there is nothing to select.
  -->
  <div class="flex min-h-screen items-center justify-center px-4">
    <div class="text-center" role="status" aria-live="polite">
      <div class="mx-auto flex h-10 w-10 items-center justify-center">
        <span class="h-5 w-5 animate-spin rounded-full border-2 border-ink-700 border-t-accent-500"></span>
      </div>
      <p class="mt-3 text-sm text-ink-400">Restoring your session…</p>
    </div>
  </div>
{:else if $session}
  <div class="flex min-h-screen">
    <AppSidebar />

    <main class="min-w-0 flex-1">
      <div class="mx-auto max-w-5xl px-5 py-8">
        {#if $workspacesError}
          <!--
            The workspace list could not be fetched, so no page can work. The
            cause is shown once here instead of separately in every panel, and
            the code names the layer that failed: `api_unreachable` is a missing
            API, not a missing workspace.
          -->
          <ErrorNotice
            message={$workspacesError.message}
            code={$workspacesError.code ?? 'workspaces_unavailable'}
          />
        {:else if blocked === 'no_workspace'}
          <ErrorNotice
            message="Your account has no workspace yet. Reload the page, or sign in again to finish setup."
            code="no_workspace"
          />
        {:else if !canRenderPage}
          <!--
            Workspace list still in flight. Deliberately quiet: this is the
            ordinary first-paint state, and an empty frame for a few hundred
            milliseconds is less alarming than an error that disappears.
          -->
          <div class="flex items-center justify-center py-16" role="status" aria-live="polite">
            <span
              class="h-5 w-5 animate-spin rounded-full border-2 border-ink-700 border-t-accent-500"
            ></span>
            <span class="ml-3 text-sm text-ink-400">Opening your workspace…</span>
          </div>
        {:else}
          {@render children()}
        {/if}
      </div>
    </main>
  </div>
{/if}
