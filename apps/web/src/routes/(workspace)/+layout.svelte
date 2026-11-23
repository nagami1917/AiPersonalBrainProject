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
  import { loadWorkspaces } from '$lib/stores/workspace';
  import AppSidebar from '$lib/components/layout/AppSidebar.svelte';

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
        {@render children()}
      </div>
    </main>
  </div>
{/if}
