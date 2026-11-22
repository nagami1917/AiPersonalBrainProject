<script lang="ts">
  /**
   * Where a source is in the pipeline, with the real stage named.
   *
   * A bare spinner is the dishonest version of this: it tells the user
   * "something is happening" when the backend knows exactly which of
   * extracting → normalising → chunking → embedding → entities it is on. Naming
   * the stage is what makes a slow document diagnosable instead of mysterious.
   *
   * The bar is derived from the stage, not a timer, so it cannot reach 100%
   * while the work is still going.
   */
  import type { DocumentStatus, ProcessingStage } from '$lib/api/types';
  import { STAGE_LABEL, STATUS_LABEL } from '$lib/api/types';
  import { cx } from '$lib/utils/cx';

  let {
    status,
    stage = null,
    progress = null,
    errorMessage = null,
    compact = false
  }: {
    status: DocumentStatus;
    stage?: ProcessingStage;
    /** Server-reported percentage, when one is available from /status. */
    progress?: number | null;
    errorMessage?: string | null;
    compact?: boolean;
  } = $props();

  // Mirrors the API's _STAGE_PROGRESS so a polled percentage and a locally
  // derived one agree. Only used when the server has not sent a number.
  const STAGE_PCT: Record<Exclude<ProcessingStage, null>, number> = {
    extracting: 20,
    chunking: 45,
    embedding: 70,
    entities: 90
  };

  const derivedPct = $derived.by(() => {
    if (status === 'ready') return 100;
    if (status === 'failed') return 100;
    if (progress !== null) return progress;
    if (status === 'pending') return 5;
    if (stage) return STAGE_PCT[stage];
    return 10;
  });

  const tone = $derived(
    status === 'ready'
      ? 'text-emerald-400'
      : status === 'failed'
        ? 'text-red-400'
        : 'text-accent-400'
  );

  const label = $derived(stage ? STAGE_LABEL[stage] : STATUS_LABEL[status]);
</script>

{#if compact}
  <span
    class={cx(
      'inline-flex items-center gap-1.5 rounded-full bg-ink-700/40 px-2 py-0.5 text-[11px] font-medium ring-1 ring-inset ring-ink-600/40',
      tone
    )}
  >
    {#if status === 'processing' || status === 'pending'}
      <span class="h-1.5 w-1.5 animate-pulse rounded-full bg-current"></span>
    {/if}
    {label}
  </span>
{:else}
  <div class="space-y-1.5">
    <div class="flex items-center justify-between text-[11px]">
      <span class={tone}>{label}</span>
      {#if status === 'processing' || status === 'pending'}
        <span class="tabular-nums text-ink-500">{derivedPct}%</span>
      {/if}
    </div>

    {#if status === 'processing' || status === 'pending'}
      <div
        class="h-1 w-full overflow-hidden rounded-full bg-ink-800"
        role="progressbar"
        aria-valuenow={derivedPct}
        aria-valuemin="0"
        aria-valuemax="100"
        aria-label="{label}, {derivedPct}%"
      >
        <div
          class="h-full rounded-full bg-accent-600 transition-all duration-500"
          style="width: {derivedPct}%"
        ></div>
      </div>
      <p class="text-[10px] text-ink-500">
        Retrieval will not use this source until it is ready.
      </p>
    {/if}

    {#if status === 'failed' && errorMessage}
      <p class="rounded border border-red-900/60 bg-red-950/30 px-2 py-1 text-[11px] text-red-300">
        {errorMessage}
      </p>
    {/if}
  </div>
{/if}
