<script lang="ts">
  /** Document status, with the real processing stage when one is known.
   * A spinner with no stage is exactly the dishonesty this avoids. */
  import type { DocumentStatus, ProcessingStage } from '$lib/api/types';
  import { STAGE_LABEL, STATUS_LABEL } from '$lib/api/types';

  let { status, stage = null }: { status: DocumentStatus; stage?: ProcessingStage } = $props();

  const tone = $derived(
    status === 'ready'
      ? 'text-emerald-400 bg-emerald-500/10 ring-emerald-500/20'
      : status === 'failed'
        ? 'text-red-400 bg-red-500/10 ring-red-500/20'
        : status === 'processing'
          ? 'text-accent-400 bg-accent-500/10 ring-accent-500/20'
          : 'text-ink-300 bg-ink-700/40 ring-ink-600/40'
  );

  const text = $derived(stage ? STAGE_LABEL[stage] : STATUS_LABEL[status]);
</script>

<span
  class="inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-[11px] font-medium ring-1 ring-inset {tone}"
>
  {#if status === 'processing'}
    <span class="h-1.5 w-1.5 animate-pulse rounded-full bg-current"></span>
  {/if}
  {text}
</span>
