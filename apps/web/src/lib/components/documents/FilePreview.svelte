<!--
  A preview of the *file*, for the formats a browser can actually render.

  This is deliberately separate from `DocumentPreview`, which shows the
  extracted text. They answer different questions and both are worth having:

    - DocumentPreview: "what will the system cite?" — the chunks, the text.
    - FilePreview:     "is this the right file?" — the pages, the layout.

  ## Why not everything

  The spec says "where supported", and that qualifier is doing real work. A
  browser natively renders PDF and raster images. It does not render DOCX, and
  for TXT/Markdown the extracted-text panel is strictly better than an iframe
  of raw bytes. So this component decides by MIME type and *declines* the rest
  rather than pretending — a broken embed box is worse than a clear statement
  that the text panel is the view.

  ## Why the signed URL, not the storage path

  The bucket is private. A preview URL is fetched server-side
  (`/download-url`) and expires in about a minute, which is why it is requested
  on demand and not rendered into the page on load: a URL minted at page load
  would be dead by the time a reader scrolls to it. The `download` parameter is
  omitted so the browser displays the file inline instead of downloading it.
-->
<script lang="ts">
  import { onDestroy } from 'svelte';
  import { apiFetch, NexusApiError } from '$lib/api/client';
  import { wsPath } from '$lib/stores/workspace';
  import type { DocumentDetail } from '$lib/api/types';

  let { document }: { document: DocumentDetail } = $props();

  let url = $state<string | null>(null);
  let loading = $state(false);
  let error = $state<string | null>(null);

  const kind = $derived(previewKind(document.mime_type));

  async function load() {
    if (loading || url) return;
    loading = true;
    error = null;
    try {
      const response = await apiFetch<{ url: string }>(
        wsPath(`/documents/${document.id}/download-url`)
      );
      url = response.url;
    } catch (err) {
      error =
        err instanceof NexusApiError ? err.message : 'Could not prepare a preview for this file.';
    } finally {
      loading = false;
    }
  }

  function close() {
    // Drop the reference: the URL is short-lived and holding it after the user
    // closes the panel only keeps a stale link in memory.
    url = null;
  }

  onDestroy(close);

  function previewKind(mime: string | null | undefined): 'pdf' | 'image' | 'unsupported' {
    if (!mime) return 'unsupported';
    if (mime === 'application/pdf') return 'pdf';
    if (mime.startsWith('image/')) return 'image';
    return 'unsupported';
  }
</script>

<section class="space-y-2">
  <div class="flex items-center justify-between">
    <h2 class="text-xs font-medium uppercase tracking-wide text-ink-500">Preview</h2>
    {#if url}
      <button class="text-[11px] text-ink-400 hover:underline" onclick={close}>Close preview</button>
    {/if}
  </div>

  {#if kind === 'unsupported'}
    <!-- Said plainly rather than shown as a broken frame. DOCX has no browser
         renderer; for plain text the extracted-text panel below is the better
         view, and duplicating it here would suggest they could disagree. -->
    <p class="rounded-xl border border-ink-800 px-4 py-4 text-[11px] text-ink-500">
      No inline preview for this format — the browser cannot render it. The
      extracted text below is what NEXUS will cite, and “Download” gives you the
      original file.
    </p>
  {:else if url}
    {#if kind === 'pdf'}
      <iframe
        src={url}
        title="Preview of {document.filename ?? document.title}"
        class="h-[32rem] w-full rounded-xl border border-ink-800 bg-ink-900/40"
      ></iframe>
    {:else}
      <img
        src={url}
        alt="Preview of {document.filename ?? document.title}"
        class="max-h-[32rem] w-auto rounded-xl border border-ink-800 bg-ink-900/40 object-contain"
      />
    {/if}
    <p class="text-[10px] text-ink-600">
      Signed link, expires shortly. Reload to re-open.
    </p>
  {:else}
    <button
      type="button"
      class="w-full rounded-xl border border-dashed border-ink-700 px-4 py-6 text-center text-xs text-ink-400 transition-colors hover:border-accent-600 hover:text-ink-200"
      onclick={load}
      disabled={loading}
    >
      {#if loading}
        Preparing preview…
      {:else}
        Show {kind === 'pdf' ? 'PDF' : 'image'} preview
      {/if}
    </button>
    {#if error}
      <p class="rounded border border-red-900/60 bg-red-950/30 px-3 py-2 text-[11px] text-red-300">
        {error}
      </p>
    {/if}
  {/if}
</section>
