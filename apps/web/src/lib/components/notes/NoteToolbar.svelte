<script lang="ts">
  /**
   * The note's control strip: title, save state, and the actions.
   *
   * The save state is always visible while editing. A silent autosave that
   * failed is worse than no autosave — the user closes the tab believing their
   * work is safe, which is the one outcome this component exists to prevent.
   */
  import type { DocumentStatus, ProcessingStage } from '$lib/api/types';
  import type { SaveState } from '$lib/utils/autosave';
  import { SAVE_LABEL } from '$lib/utils/autosave';
  import ProcessingStatus from '$lib/components/documents/ProcessingStatus.svelte';
  import { cx } from '$lib/utils/cx';
  import { relativeTime } from '$lib/utils/format';

  let {
    title = $bindable(''),
    status,
    stage = null,
    updatedAt,
    chunkCount = 0,
    embeddingModel = null,
    saveState = 'idle',
    editable = true,
    saving = false,
    onsave,
    ondelete
  }: {
    title?: string;
    status: DocumentStatus;
    stage?: ProcessingStage;
    updatedAt: string;
    chunkCount?: number;
    embeddingModel?: string | null;
    saveState?: SaveState;
    editable?: boolean;
    saving?: boolean;
    onsave: () => void;
    ondelete: () => void;
  } = $props();

  const tone = $derived(
    saveState === 'error'
      ? 'text-red-400'
      : saveState === 'saving' || saveState === 'dirty'
        ? 'text-amber-400'
        : 'text-emerald-400'
  );
</script>

<header class="flex flex-wrap items-start justify-between gap-3">
  <div class="min-w-0 flex-1">
    <input
      class="w-full border-0 bg-transparent p-0 text-xl font-semibold tracking-tight text-ink-50 focus:outline-none disabled:text-ink-50"
      bind:value={title}
      disabled={!editable}
      maxlength={500}
      aria-label="Note title"
    />
    <p class="mt-1 flex flex-wrap items-center gap-2 text-[11px] text-ink-500">
      <span>updated {relativeTime(updatedAt)}</span>
      <span>·</span>
      <span>{chunkCount} passages indexed</span>
      {#if embeddingModel}
        <span>·</span>
        <span class="font-mono">{embeddingModel}</span>
      {/if}
      {#if editable}
        <span>·</span>
        <span class={cx(tone)} role="status" aria-live="polite">{SAVE_LABEL[saveState]}</span>
      {/if}
    </p>
  </div>

  <div class="flex shrink-0 items-center gap-2">
    <ProcessingStatus {status} {stage} compact />
    {#if editable}
      <button class="btn-primary px-3 py-1.5 text-xs" onclick={onsave} disabled={saving}>
        Save
      </button>
      <button class="btn-ghost px-2 py-1.5 text-xs text-red-400" onclick={ondelete}>
        Delete
      </button>
    {/if}
  </div>
</header>
