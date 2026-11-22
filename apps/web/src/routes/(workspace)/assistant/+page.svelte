<script lang="ts">
  /**
   * The product's core surface.
   *
   * Three behaviours are deliberate:
   *  - "Reading N sources" is shown BEFORE the answer streams, because that is
   *    when retrieval actually happened.
   *  - Citation chips render from a structured citations payload; markers the
   *    server stripped never appear.
   *  - An ungrounded answer is styled as a refusal, not as an answer.
   */
  import { streamChat, type ChatEvent, type Citation, type SourcePreview } from '$lib/api/client';
  import AnswerBody from '$lib/components/AnswerBody.svelte';
  import { cx } from '$lib/utils/cx';
  import { sourceHref, sourceLabel } from '$lib/utils/sources';
  import { activeWorkspaceId } from '$lib/stores/workspace';

  type Turn = {
    id: string;
    question: string;
    answer: string;
    sources: SourcePreview[];
    citations: Citation[];
    grounded: boolean | null;
    streaming: boolean;
  };

  let turns = $state<Turn[]>([]);
  let question = $state('');
  let conversationId = $state<string | null>(null);
  let busy = $state(false);
  let error = $state<string | null>(null);
  let controller: AbortController | null = null;

  async function ask() {
    const text = question.trim();
    const workspaceId = $activeWorkspaceId;
    if (!text || busy || !workspaceId) return;

    error = null;
    busy = true;
    question = '';

    const turn: Turn = {
      id: crypto.randomUUID(),
      question: text,
      answer: '',
      sources: [],
      citations: [],
      grounded: null,
      streaming: true
    };
    turns = [...turns, turn];

    controller = new AbortController();

    try {
      await streamChat(
        workspaceId,
        { conversation_id: conversationId, message: text },
        (event: ChatEvent) => {
          const target = turns.find((t) => t.id === turn.id);
          if (!target) return;

          if (event.type === 'sources') {
            target.sources = event.sources;
          } else if (event.type === 'delta') {
            target.answer += event.text;
          } else if (event.type === 'citations') {
            target.citations = event.citations;
            target.grounded = event.grounded;
          } else if (event.type === 'done') {
            conversationId = event.conversation_id;
            target.streaming = false;
          } else if (event.type === 'error') {
            error = event.message;
            target.streaming = false;
          }
          turns = [...turns];
        },
        controller.signal
      );
    } catch (err) {
      error = err instanceof Error ? err.message : 'The answer was interrupted.';
      const target = turns.find((t) => t.id === turn.id);
      if (target) target.streaming = false;
      turns = [...turns];
    } finally {
      busy = false;
      controller = null;
    }
  }

  function onKeydown(event: KeyboardEvent) {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      void ask();
    }
  }

  const suggestions = [
    'Where do my deployment secrets live?',
    'What did I decide about retrieval scoring?',
    'Summarize what I know about the job queue.'
  ];
</script>

<svelte:head><title>Assistant · NEXUS</title></svelte:head>

<div class="flex min-h-[calc(100vh-4rem)] flex-col">
  <header class="mb-6">
    <h1 class="text-xl font-semibold tracking-tight text-ink-50">Assistant</h1>
    <p class="mt-1 text-sm text-ink-400">
      Answers come only from your documents. If nothing relevant exists, it will say so.
    </p>
  </header>

  <div class="flex-1 space-y-8">
    {#if turns.length === 0}
      <div class="card p-8 text-center">
        <p class="text-sm text-ink-300">Ask something about your own knowledge.</p>
        <div class="mt-4 flex flex-wrap justify-center gap-2">
          {#each suggestions as suggestion}
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
      <article class="space-y-3">
        <p class="text-sm font-medium text-ink-200">{turn.question}</p>

        {#if turn.sources.length > 0}
          <p class="text-[11px] text-ink-500">
            Reading {turn.sources.length}
            {turn.sources.length === 1 ? 'source' : 'sources'}:
            {turn.sources.map((s) => s.source_title).join(', ')}
          </p>
        {/if}

        <div
          class={cx(
            'rounded-xl border px-4 py-3',
            turn.grounded === false
              ? 'border-amber-900/50 bg-amber-950/20'
              : 'border-ink-800 bg-ink-900/50'
          )}
        >
          {#if turn.answer}
            <AnswerBody content={turn.answer} citations={turn.citations} />
          {:else if turn.streaming}
            <div class="flex items-center gap-2 text-xs text-ink-500">
              <span class="h-1.5 w-1.5 animate-pulse rounded-full bg-accent-500"></span>
              Searching your documents…
            </div>
          {/if}

          {#if turn.grounded === false}
            <p class="mt-2 border-t border-amber-900/40 pt-2 text-[11px] text-amber-400/80">
              No supporting passage was found, so no answer was generated.
            </p>
          {/if}
        </div>

        {#if turn.citations.length > 0}
          <ul class="space-y-1.5">
            {#each turn.citations as citation (citation.marker)}
              <li class="flex gap-2 text-[11px] text-ink-400">
                <span class="cite-chip">{citation.marker}</span>
                <span class="min-w-0">
                  <span class="text-[10px] uppercase tracking-wide text-ink-500">
                    {sourceLabel(citation.source_kind)}
                  </span>
                  <a
                    href={sourceHref(citation.source_kind, citation.source_id)}
                    class="text-ink-200 hover:text-accent-400">{citation.source_title}</a>
                  {#if citation.page}<span class="text-ink-500"> · p.{citation.page}</span>{/if}
                  <span class="mt-0.5 block truncate italic text-ink-500">“{citation.quote}”</span>
                </span>
              </li>
            {/each}
          </ul>
        {/if}
      </article>
    {/each}
  </div>

  {#if error}
    <p class="mt-4 rounded-lg border border-red-900/60 bg-red-950/30 px-3 py-2 text-xs text-red-300">
      {error}
    </p>
  {/if}

  <div class="sticky bottom-0 mt-6 bg-ink-950/95 pb-4 pt-3">
    <div class="flex items-end gap-2">
      <textarea
        class="input min-h-[44px] resize-none"
        rows="1"
        placeholder="Ask about your documents…"
        bind:value={question}
        onkeydown={onKeydown}
        disabled={busy}
      ></textarea>
      <button class="btn-primary h-11 px-4" onclick={ask} disabled={busy || !question.trim()}>
        {busy ? '…' : 'Ask'}
      </button>
    </div>
    <p class="mt-1.5 text-[11px] text-ink-600">Enter to send · Shift+Enter for a new line</p>
  </div>
</div>
