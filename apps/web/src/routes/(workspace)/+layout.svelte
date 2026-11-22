<script lang="ts">
  /**
   * The workspace shell: session gate, workspace list, and the two-column frame.
   *
   * It owns nothing visual beyond the frame — navigation lives in AppSidebar and
   * the page header lives in Topbar, so each can change without touching the
   * auth gate this file exists for.
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

<div class="flex min-h-screen">
  <AppSidebar />

  <main class="min-w-0 flex-1">
    <div class="mx-auto max-w-5xl px-5 py-8">
      {@render children()}
    </div>
  </main>
</div>
