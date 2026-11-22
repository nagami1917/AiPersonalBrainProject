<script lang="ts">
  /**
   * The bar above the page content: where you are, and whatever the page wants
   * to put on the right.
   *
   * It shows the workspace name on narrow screens, where the sidebar is hidden,
   * so the one piece of context that matters — *which tenant am I in* — is
   * never invisible.
   */
  import type { Snippet } from 'svelte';
  import { activeWorkspace, workspacesLoading } from '$lib/stores/workspace';

  let {
    title = '',
    subtitle = '',
    actions
  }: { title?: string; subtitle?: string; actions?: Snippet } = $props();
</script>

<header class="mb-6 flex flex-wrap items-end justify-between gap-3 border-b border-ink-800 pb-4">
  <div class="min-w-0">
    {#if title}
      <h1 class="truncate text-xl font-semibold tracking-tight text-ink-50">{title}</h1>
    {/if}
    {#if subtitle}
      <p class="mt-1 text-sm text-ink-400">{subtitle}</p>
    {/if}
    <p class="mt-1 text-[11px] text-ink-500 lg:hidden">
      {#if $workspacesLoading}
        Loading workspace…
      {:else}
        {$activeWorkspace?.name ?? 'No workspace'}
      {/if}
    </p>
  </div>

  {#if actions}
    <div class="flex shrink-0 items-center gap-2">
      {@render actions()}
    </div>
  {/if}
</header>
