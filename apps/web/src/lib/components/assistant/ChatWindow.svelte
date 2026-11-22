<script lang="ts">
  /**
   * The conversation surface: turn list, empty state with starter questions,
   * and the composer.
   *
   * It does not own the stream — the page does, because the page also owns the
   * conversation id and the abort controller. This component is presentational
   * plus one piece of behaviour it must own: scrolling the newest turn into
   * view as text arrives.
   */
  import MessageBubble, { type Turn } from './MessageBubble.svelte';
  import ChatInput from './ChatInput.svelte';

  let {
    turns = [],
    busy = false,
    error = null,
    question = $bindable(''),
    onsend,
    suggestions = []
  }: {
    turns?: Turn[];
    busy?: boolean;
    error?: string | null;
    question?: string;
    onsend: () => void;
    suggestions?: string[];
  } = $props();

  let end = $state<HTMLDivElement | null>(null);

  // Follow the stream. `scrollIntoView` on a sentinel beats measuring heights:
  // it stays correct as the answer grows and needs no layout reads per delta.
  $effect(() => {
    if (turns.length > 0) {
      end?.scrollIntoView({ behavior: 'smooth', block: 'end' });
    }
  });
</script>

<div class="flex min-h-[calc(100vh-4rem)] flex-col">
  <div class="flex-1 space-y-8">
    {#if turns.length === 0}
      <div class="card p-8 text-center">
        <p class="text-sm text-ink-300">Ask something about your own knowledge.</p>
        <div class="mt-4 flex flex-wrap justify-center gap-2">
          {#each suggestions as suggestion (suggestion)}
            <button
              class="rounded-full border border-ink-700 px-3 py-1.5 text-xs text-ink-300 hover:border-accent-600 hover:text-accent-400"
              onclick={() => (question = suggestion)}
            >
              {suggestion}
            </button>
          {/each}
        </div>
      </div>
    {/if}

    {#each turns as turn (turn.id)}
      <MessageBubble {turn} />
    {/each}

    <div bind:this={end}></div>
  </div>

  {#if error}
    <p class="mt-4 rounded-lg border border-red-900/60 bg-red-950/30 px-3 py-2 text-xs text-red-300">
      {error}
    </p>
  {/if}

  <ChatInput bind:value={question} {busy} {onsend} />
</div>
