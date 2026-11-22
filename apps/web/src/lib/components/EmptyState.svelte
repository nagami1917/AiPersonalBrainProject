<script lang="ts">
  import type { Snippet } from 'svelte';

  let {
    title,
    description,
    children
  }: { title: string; description: string; children?: Snippet } = $props();
</script>

<div class="flex flex-col items-center justify-center rounded-xl border border-dashed border-ink-700 px-6 py-16 text-center">
  <h3 class="text-sm font-medium text-ink-200">{title}</h3>
  <p class="mt-1 max-w-sm text-sm text-ink-400">{description}</p>
  {#if children}
    <div class="mt-4">{@render children()}</div>
  {/if}
</div>
