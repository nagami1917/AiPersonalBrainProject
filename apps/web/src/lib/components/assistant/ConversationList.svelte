<script lang="ts">
  /**
   * Conversation history for the assistant.
   *
   * The backend has always had `GET/PATCH/DELETE /conversations`; nothing in the
   * UI called them, so leaving the page discarded the thread and the assistant
   * behaved as if persistence did not exist. This is the missing half.
   *
   * Titles are set server-side from the opening question, so a row is labelled
   * without an extra request. A `null` title is still possible for a row created
   * before that change, which is why the fallback exists rather than being
   * defensive noise.
   */
  import type { ConversationSummary } from '$lib/api/client';

  let {
    conversations,
    activeId = null,
    busy = false,
    onselect,
    onrename,
    ondelete
  }: {
    conversations: ConversationSummary[];
    activeId?: string | null;
    busy?: boolean;
    onselect: (id: string) => void;
    onrename: (id: string, title: string) => Promise<void>;
    ondelete: (id: string) => Promise<void>;
  } = $props();

  let editingId = $state<string | null>(null);
  let draft = $state('');

  function label(conversation: ConversationSummary): string {
    return conversation.title?.trim() || 'Untitled conversation';
  }

  function startRename(conversation: ConversationSummary) {
    editingId = conversation.id;
    draft = conversation.title ?? '';
  }

  async function commitRename(id: string) {
    const title = draft.trim();
    editingId = null;
    // An empty title would make the row unfindable, so a blank edit is a cancel.
    if (!title) return;
    await onrename(id, title);
  }
</script>

<aside class="w-full shrink-0 md:w-56">
  <h2 class="mb-2 text-[10px] font-semibold uppercase tracking-wider text-ink-500">History</h2>

  {#if conversations.length === 0}
    <p class="text-xs text-ink-600">Questions you ask are saved here.</p>
  {:else}
    <ul class="space-y-0.5">
      {#each conversations as conversation (conversation.id)}
        <li>
          {#if editingId === conversation.id}
            <form
              class="flex gap-1"
              onsubmit={(event) => {
                event.preventDefault();
                commitRename(conversation.id);
              }}
            >
              <input
                bind:value={draft}
                aria-label="Conversation title"
                class="min-w-0 flex-1 rounded border border-ink-700 bg-ink-900 px-1.5 py-1 text-xs text-ink-100"
                onblur={() => commitRename(conversation.id)}
              />
            </form>
          {:else}
            <div
              class="group flex items-center gap-1 rounded px-1.5 py-1 text-xs {activeId ===
              conversation.id
                ? 'bg-ink-800 text-ink-100'
                : 'text-ink-400 hover:bg-ink-900'}"
            >
              <button
                type="button"
                class="min-w-0 flex-1 truncate text-left"
                title={label(conversation)}
                disabled={busy}
                onclick={() => onselect(conversation.id)}
              >
                {label(conversation)}
              </button>
              <button
                type="button"
                class="opacity-0 transition group-hover:opacity-100 focus-visible:opacity-100"
                aria-label="Rename conversation"
                onclick={() => startRename(conversation)}
              >
                ✎
              </button>
              <button
                type="button"
                class="opacity-0 transition group-hover:opacity-100 focus-visible:opacity-100"
                aria-label="Delete conversation"
                onclick={() => ondelete(conversation.id)}
              >
                ✕
              </button>
            </div>
          {/if}
        </li>
      {/each}
    </ul>
  {/if}
</aside>
