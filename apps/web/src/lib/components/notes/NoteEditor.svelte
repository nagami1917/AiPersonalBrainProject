<script lang="ts">
  /**
   * Markdown source and live preview, side by side.
   *
   * Side by side rather than a toggle: Markdown's value is that the source stays
   * readable, and a mode switch makes formatting a guess — you type `**bold**`
   * and then have to switch view to find out whether it worked.
   *
   * The preview uses the same parser that renders the saved note, so what is on
   * screen during editing is what comes back after a reload. The wiki-link map
   * comes from the *server's* resolved links, not from re-parsing here, because
   * a second resolution implementation would eventually disagree with the save.
   */
  import MarkdownView from '$lib/components/notes/MarkdownView.svelte';
  import { extractWikiLinks } from '$lib/utils/markdown';

  let {
    body = $bindable(''),
    editable = true,
    wikiLinks = new Map<string, string>(),
    oninput
  }: {
    body?: string;
    editable?: boolean;
    wikiLinks?: Map<string, string>;
    oninput?: (value: string) => void;
  } = $props();

  const draftLinks = $derived(extractWikiLinks(body));
</script>

<div class="grid gap-4 lg:grid-cols-2">
  <div class="space-y-1.5">
    <div class="flex items-center justify-between">
      <p class="text-[11px] uppercase tracking-wide text-ink-500">Markdown</p>
      <p class="text-[10px] text-ink-600">
        {draftLinks.length} link{draftLinks.length === 1 ? '' : 's'} in this draft
      </p>
    </div>
    <textarea
      class="input min-h-[26rem] resize-y font-mono text-[13px] leading-relaxed"
      bind:value={body}
      oninput={(e) => oninput?.((e.currentTarget as HTMLTextAreaElement).value)}
      disabled={!editable}
      spellcheck="false"
      aria-label="Note body, Markdown"
    ></textarea>
    <p class="text-[10px] text-ink-600">
      **bold** · *italic* · `code` · # heading · - list · &gt; quote · [[Note title]] to link
    </p>
  </div>

  <div class="space-y-1.5">
    <p class="text-[11px] uppercase tracking-wide text-ink-500">Preview</p>
    <div class="min-h-[26rem] rounded-lg border border-ink-800 bg-ink-900/40 px-4 py-3">
      {#if body.trim()}
        <MarkdownView source={body} {wikiLinks} />
      {:else}
        <p class="text-xs text-ink-600">Nothing written yet.</p>
      {/if}
    </div>
  </div>
</div>
