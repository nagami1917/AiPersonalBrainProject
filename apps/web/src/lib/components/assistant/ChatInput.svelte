<script lang="ts">
  /**
   * The composer.
   *
   * Enter sends, Shift+Enter makes a new line — stated under the field, because
   * getting that backwards loses a long question. `disabled` while a stream is
   * running rather than queueing a second question: the server holds one
   * transaction open per stream, and two answers interleaved would be unreadable.
   */
  let {
    value = $bindable(''),
    busy = false,
    placeholder = 'Ask about your documents…',
    onsend
  }: {
    value?: string;
    busy?: boolean;
    placeholder?: string;
    onsend: () => void;
  } = $props();

  function handleKeydown(event: KeyboardEvent) {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      if (!busy && value.trim()) onsend();
    }
  }
</script>

<div class="sticky bottom-0 mt-6 bg-ink-950/95 pb-4 pt-3">
  <div class="flex items-end gap-2">
    <textarea
      class="input min-h-[44px] resize-none"
      rows="1"
      {placeholder}
      bind:value
      onkeydown={handleKeydown}
      disabled={busy}
      aria-label="Ask a question"
    ></textarea>
    <button class="btn-primary h-11 px-4" onclick={onsend} disabled={busy || !value.trim()}>
      {busy ? '…' : 'Ask'}
    </button>
  </div>
  <p class="mt-1.5 text-[11px] text-ink-600">Enter to send · Shift+Enter for a new line</p>
</div>
