<script lang="ts">
  /** One error renderer for the whole app, matching the API's error envelope. */
  let { message, code }: { message: string; code?: string } = $props();
</script>

<div class="rounded-lg border border-red-900/60 bg-red-950/30 px-4 py-3 text-sm" role="alert">
  <p class="text-red-300">{message}</p>
  {#if code}
    <p class="mt-1 font-mono text-[11px] text-red-400/70">{code}</p>
  {/if}
</div>
