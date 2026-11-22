<script lang="ts">
  /**
   * Quick capture: get a thought in without leaving the page.
   *
   * The reference shows Web / Note / File tabs with a URL and a Save button.
   * Three deliberate differences here:
   *
   *  - **Web capture is not offered.** Nothing in the backend fetches a URL yet,
   *    and a "Web" tab that silently saved a bookmark with no content would be a
   *    feature that looks real and does nothing. The tab appears only when the
   *    capture endpoint exists. Note and File work today.
   *  - **A note is created as a real note**, not an inbox item, so it is
   *    immediately indexed and citable like anything else.
   *  - **File goes through the same signed-URL flow** as the Documents page —
   *    no second upload path that could drift on validation.
   */
  import { apiFetch, NexusApiError } from '$lib/api/client';
  import { uploadFile } from '$lib/api/supabase';
  import { wsPath } from '$lib/stores/workspace';
  import type { Meta, NoteSummary } from '$lib/api/types';

  let { oncaptured }: { oncaptured?: () => void } = $props();

  type Mode = 'note' | 'file';

  let mode = $state<Mode>('note');
  let title = $state('');
  let body = $state('');
  let busy = $state(false);
  let status = $state<{ kind: 'ok' | 'error'; message: string } | null>(null);
  let meta = $state<Meta | null>(null);
  let fileInput = $state<HTMLInputElement | null>(null);

  $effect(() => {
    // The size/type limits are the API's, so they are fetched rather than
    // duplicated here where they would go stale.
    void apiFetch<Meta>('/v1/meta')
      .then((value) => (meta = value))
      .catch(() => (meta = null));
  });

  async function captureNote() {
    const text = body.trim();
    if (!text || busy) return;
    busy = true;
    status = null;
    try {
      // A title is required by the API; the first line is the natural one, and
      // trimming to 500 keeps a pasted paragraph from being rejected.
      const derived = (title.trim() || text.split('\n')[0]).slice(0, 500);
      await apiFetch<{ note: NoteSummary }>(wsPath('/notes'), {
        method: 'POST',
        body: { title: derived, content: text }
      });
      title = '';
      body = '';
      status = { kind: 'ok', message: 'Captured. It is being indexed now.' };
      oncaptured?.();
    } catch (err) {
      status = {
        kind: 'error',
        message:
          err instanceof NexusApiError ? err.message : 'Could not save that. Please try again.'
      };
    } finally {
      busy = false;
    }
  }

  async function captureFile(files: FileList | null) {
    const file = files?.[0];
    if (!file || busy) return;
    busy = true;
    status = null;
    try {
      const { document_id, file_path } = await apiFetch<{ document_id: string; file_path: string }>(
        wsPath('/documents/upload-url'),
        {
          method: 'POST',
          body: {
            filename: file.name,
            mime_type: file.type || 'text/plain',
            file_size: file.size
          }
        }
      );
      await uploadFile(file_path, file);
      await apiFetch(wsPath('/documents'), {
        method: 'POST',
        body: {
          document_id,
          title: file.name.replace(/\.[^.]+$/, ''),
          filename: file.name,
          collection_id: null
        }
      });
      status = { kind: 'ok', message: `Added ${file.name}.` };
      oncaptured?.();
    } catch (err) {
      status = {
        kind: 'error',
        message:
          err instanceof NexusApiError ? `${file.name}: ${err.message}` : 'Could not upload that file.'
      };
    } finally {
      busy = false;
      if (fileInput) fileInput.value = '';
    }
  }

  function onKeydown(event: KeyboardEvent) {
    if (event.key === 'Enter' && (event.metaKey || event.ctrlKey)) {
      event.preventDefault();
      void captureNote();
    }
  }
</script>

<section class="card flex flex-col p-4">
  <div class="flex items-center justify-between">
    <h2 class="text-sm font-medium text-ink-200">Capture</h2>
    <div class="flex gap-1 rounded-lg bg-ink-900 p-0.5">
      <button
        class="rounded px-2 py-1 text-[11px] transition-colors {mode === 'note'
          ? 'bg-ink-700 text-ink-50'
          : 'text-ink-400 hover:text-ink-200'}"
        onclick={() => (mode = 'note')}
      >
        Note
      </button>
      <button
        class="rounded px-2 py-1 text-[11px] transition-colors {mode === 'file'
          ? 'bg-ink-700 text-ink-50'
          : 'text-ink-400 hover:text-ink-200'}"
        onclick={() => (mode = 'file')}
      >
        File
      </button>
    </div>
  </div>

  {#if mode === 'note'}
    <input
      class="input mt-3"
      placeholder="Title (optional — the first line is used)"
      bind:value={title}
      maxlength={500}
      aria-label="Capture title"
    />
    <textarea
      class="input mt-2 min-h-24 resize-y"
      placeholder="Write something worth keeping…"
      bind:value={body}
      onkeydown={onKeydown}
      aria-label="Capture body"
    ></textarea>
    <div class="mt-2 flex items-center justify-between gap-2">
      <p class="text-[10px] text-ink-600">⌘/Ctrl + Enter to save</p>
      <button class="btn-primary px-3 py-1.5 text-xs" onclick={captureNote} disabled={busy || !body.trim()}>
        {busy ? 'Saving…' : 'Save'}
      </button>
    </div>
  {:else}
    <label
      class="mt-3 flex cursor-pointer flex-col items-center justify-center rounded-lg border border-dashed border-ink-700 px-4 py-8 text-center hover:border-accent-600"
    >
      <input
        bind:this={fileInput}
        type="file"
        class="sr-only"
        accept={meta?.supported_mime_types.join(',') ?? ''}
        onchange={(e) => captureFile((e.currentTarget as HTMLInputElement).files)}
      />
      <span class="text-xs text-ink-200">{busy ? 'Uploading…' : 'Choose a file'}</span>
      <span class="mt-1 text-[10px] text-ink-500">
        {meta ? meta.supported_mime_types.join(', ') : 'PDF, DOCX, TXT, Markdown'}
      </span>
    </label>
  {/if}

  {#if status}
    <p
      class="mt-2 text-[11px] {status.kind === 'ok' ? 'text-emerald-400' : 'text-red-400'}"
      role="status"
    >
      {status.message}
    </p>
  {/if}

  <p class="mt-auto pt-3 text-[10px] text-ink-600">Everything captured here becomes citable.</p>
</section>
