<script lang="ts">
  /**
   * Assistant page: owns the stream, the conversation id and the abort
   * controller. Everything visual lives in `components/assistant/`.
   *
   * Keeping the stream here rather than in ChatWindow is deliberate — the
   * conversation id must survive across turns, and a component that unmounts
   * would take it with it.
   */
  import { streamChat, type ChatEvent } from '$lib/api/client';
  import ChatWindow from '$lib/components/assistant/ChatWindow.svelte';
  import type { Turn } from '$lib/components/assistant/MessageBubble.svelte';
  import { activeWorkspaceId } from '$lib/stores/workspace';

  let turns = $state<Turn[]>([]);
  let question = $state('');
  let conversationId = $state<string | null>(null);
  let busy = $state(false);
  let error = $state<string | null>(null);
  let controller: AbortController | null = null;

  const suggestions = [
    'Where do my deployment secrets live?',
    'What did I decide about retrieval scoring?',
    'Summarize what I know about the job queue.'
  ];

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
</script>

<svelte:head><title>Assistant · NEXUS</title></svelte:head>

<div class="space-y-4">
  <header>
    <h1 class="text-xl font-semibold tracking-tight text-ink-50">Assistant</h1>
    <p class="mt-1 text-sm text-ink-400">
      Answers come only from your documents. If nothing relevant exists, it will say so.
    </p>
  </header>

  <ChatWindow bind:question {turns} {busy} {error} {suggestions} onsend={ask} />
</div>
