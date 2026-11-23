<script lang="ts">
  /**
   * Assistant page: owns the stream, the conversation id and the abort
   * controller. Everything visual lives in `components/assistant/`.
   *
   * Keeping the stream here rather than in ChatWindow is deliberate — the
   * conversation id must survive across turns, and a component that unmounts
   * would take it with it.
   *
   * ## Persistence
   *
   * The backend has always saved conversations; this page used to discard the id
   * on navigation, so a reload started from nothing and the saved history was
   * unreachable. It now loads the list on mount and can reopen, rename and delete
   * a thread. Reopening replays the stored messages and their citations, which is
   * what makes a past answer auditable rather than a memory.
   */
  import {
    streamChat,
    listConversations,
    getConversation,
    renameConversation,
    deleteConversation,
    type ChatEvent,
    type ConversationSummary
  } from '$lib/api/client';
  import ChatWindow from '$lib/components/assistant/ChatWindow.svelte';
  import ConversationList from '$lib/components/assistant/ConversationList.svelte';
  import type { Turn } from '$lib/components/assistant/MessageBubble.svelte';
  import { activeWorkspaceId } from '$lib/stores/workspace';

  let turns = $state<Turn[]>([]);
  let question = $state('');
  let conversationId = $state<string | null>(null);
  let conversations = $state<ConversationSummary[]>([]);
  let busy = $state(false);
  let loadingHistory = $state(false);
  let error = $state<string | null>(null);
  let controller: AbortController | null = null;

  const suggestions = [
    'Where do my deployment secrets live?',
    'What did I decide about retrieval scoring?',
    'Summarize what I know about the job queue.'
  ];

  async function refreshHistory() {
    const workspaceId = $activeWorkspaceId;
    if (!workspaceId) return;
    try {
      conversations = await listConversations(workspaceId);
    } catch {
      // History is a convenience; failing to list it must not block asking a
      // question, so this is silent rather than a page-level error.
      conversations = [];
    }
  }

  $effect(() => {
    // Re-runs when the workspace changes, which is the only time the list can be
    // different.
    if ($activeWorkspaceId) void refreshHistory();
  });

  async function open(id: string) {
    const workspaceId = $activeWorkspaceId;
    if (!workspaceId || busy || id === conversationId) return;

    controller?.abort();
    loadingHistory = true;
    error = null;
    try {
      const detail = await getConversation(workspaceId, id);
      // Stored messages arrive as a flat list; the UI is organised as
      // question/answer pairs, so they are zipped back together here.
      const rebuilt: Turn[] = [];
      for (const message of detail.messages) {
        if (message.role === 'user') {
          rebuilt.push({
            id: message.id,
            question: message.content,
            answer: '',
            sources: [],
            citations: [],
            grounded: null,
            streaming: false
          });
        } else if (rebuilt.length > 0) {
          const target = rebuilt[rebuilt.length - 1];
          target.answer = message.content;
          target.grounded = message.grounded;
          target.citations = message.citations.map((citation) => ({
            marker: citation.marker,
            chunk_id: citation.chunk_id,
            source_kind: citation.source_kind,
            source_id: citation.source_id,
            source_title: citation.source_title,
            filename: citation.filename,
            quote: citation.quote,
            page: citation.page,
            heading_path: citation.heading_path,
            relevance: citation.relevance,
            relevance_kind: citation.relevance_kind
          }));
        }
      }
      turns = rebuilt;
      conversationId = detail.id;
    } catch (err) {
      error = err instanceof Error ? err.message : 'Could not open that conversation.';
    } finally {
      loadingHistory = false;
    }
  }

  function startNew() {
    controller?.abort();
    turns = [];
    conversationId = null;
    error = null;
  }

  async function rename(id: string, title: string) {
    const workspaceId = $activeWorkspaceId;
    if (!workspaceId) return;
    await renameConversation(workspaceId, id, title);
    await refreshHistory();
  }

  async function remove(id: string) {
    const workspaceId = $activeWorkspaceId;
    if (!workspaceId) return;
    await deleteConversation(workspaceId, id);
    if (id === conversationId) startNew();
    await refreshHistory();
  }

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
            const firstTurnOfConversation = conversationId === null;
            conversationId = event.conversation_id;
            target.streaming = false;
            // A new conversation only appears in the list once it exists, so the
            // list is refreshed after the first turn. Later turns change the
            // title of nothing, so they do not need it.
            if (firstTurnOfConversation) void refreshHistory();
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
  <header class="flex flex-wrap items-start justify-between gap-2">
    <div>
      <h1 class="text-xl font-semibold tracking-tight text-ink-50">Assistant</h1>
      <p class="mt-1 text-sm text-ink-400">
        Answers come only from your documents. If nothing relevant exists, it will say so.
      </p>
    </div>
    <button
      type="button"
      class="rounded border border-ink-700 px-2.5 py-1 text-xs text-ink-300 hover:bg-ink-900 disabled:opacity-50"
      disabled={busy || turns.length === 0}
      onclick={startNew}
    >
      New question
    </button>
  </header>

  <div class="flex flex-col gap-4 md:flex-row">
    <ConversationList
      {conversations}
      activeId={conversationId}
      {busy}
      onselect={open}
      onrename={rename}
      ondelete={remove}
    />

    <div class="min-w-0 flex-1">
      {#if loadingHistory}
        <p class="mb-2 text-xs text-ink-500">Loading conversation…</p>
      {/if}
      <ChatWindow bind:question {turns} {busy} {error} {suggestions} onsend={ask} />
    </div>
  </div>
</div>
