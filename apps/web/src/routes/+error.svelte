<script lang="ts">
  /**
   * Global error boundary.
   *
   * Without this file SvelteKit renders its default error page: an unstyled
   * white screen with a stack trace in development and a bare status code in
   * production. That is the one screen in the app that would not match the
   * design, and it appears exactly when something has gone wrong.
   *
   * Two kinds of error arrive here and are deliberately distinguished:
   *
   *   404 — the URL does not exist. A wrong link, not a fault. Offer a way back.
   *   anything else — a real failure. Say so plainly, do not pretend, and do
   *   not leak the internal message to the user in production. The technical
   *   detail is shown only in dev, because a production user cannot act on it
   *   and an attacker learns from it.
   */
  import { page } from '$app/state';

  const status = $derived(page.status);
  const isNotFound = $derived(status === 404);

  // `page.error.message` is SvelteKit's safe message — for a `handle`-thrown
  // error it is the `App.Error` message, never a raw exception.
  const message = $derived(
    isNotFound
      ? 'That page does not exist.'
      : (page.error?.message ?? 'Something went wrong.')
  );
</script>

<svelte:head><title>{status} · NEXUS</title></svelte:head>

<main class="flex min-h-screen items-center justify-center px-4">
  <div class="w-full max-w-md text-center">
    <p class="font-mono text-xs uppercase tracking-widest text-ink-500">{status}</p>

    <h1 class="mt-3 text-2xl font-semibold tracking-tight text-ink-50">
      {isNotFound ? 'Nothing here' : 'Something broke'}
    </h1>

    <p class="mt-2 text-sm text-ink-400">
      {#if isNotFound}
        The page you asked for is not part of NEXUS. It may have been moved, or the link may
        be mistyped.
      {:else}
        {message}
        {#if !page.error?.code}
          <span class="block pt-2">The request did not complete. Try again, and if it keeps happening the API may be down.</span>
        {/if}
      {/if}
    </p>

    {#if page.error?.code}
      <!-- A machine-readable code the user can quote in a bug report. -->
      <p class="mt-3 font-mono text-[11px] text-ink-600">{page.error.code}</p>
    {/if}

    <div class="mt-6 flex items-center justify-center gap-3">
      <a class="btn-primary" href="/dashboard">Go to dashboard</a>
      <button class="btn-ghost" onclick={() => history.back()}>Go back</button>
    </div>
  </div>
</main>
