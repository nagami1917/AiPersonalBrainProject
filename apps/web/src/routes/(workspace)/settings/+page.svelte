<script lang="ts">
  import { onMount } from 'svelte';
  import { apiFetch, NexusApiError } from '$lib/api/client';
  import { signOut } from '$lib/stores/session';
  import { session } from '$lib/stores/session';
  import type { Meta } from '$lib/api/types';
  import ErrorNotice from '$lib/components/ErrorNotice.svelte';
  import WorkspaceMembers from '$lib/components/WorkspaceMembers.svelte';
  import { activeWorkspace } from '$lib/stores/workspace';
  import { ROLE_LABEL } from '$lib/api/types';

  let meta = $state<Meta | null>(null);
  let error = $state<{ message: string; code?: string } | null>(null);

  onMount(async () => {
    try {
      meta = await apiFetch<Meta>('/meta');
    } catch (err) {
      error =
        err instanceof NexusApiError
          ? { message: err.message, code: err.code }
          : { message: 'Could not load configuration.' };
    }
  });
</script>

<svelte:head><title>Settings · NEXUS</title></svelte:head>

<div class="max-w-2xl space-y-6">
  <header>
    <h1 class="text-xl font-semibold tracking-tight text-ink-50">Settings</h1>
  </header>

  {#if error}<ErrorNotice message={error.message} code={error.code} />{/if}

  <section class="card divide-y divide-ink-800">
    <div class="flex items-center justify-between px-4 py-3">
      <div>
        <p class="text-sm text-ink-100">Signed in as</p>
        <p class="text-xs text-ink-500">{$session?.user.email}</p>
      </div>
      <button class="btn-ghost" onclick={signOut}>Sign out</button>
    </div>
    {#if $activeWorkspace}
      <div class="px-4 py-3">
        <p class="text-sm text-ink-100">{$activeWorkspace.name}</p>
        <p class="text-xs text-ink-500">
          Your role: {ROLE_LABEL[$activeWorkspace.role]}
          {#if $activeWorkspace.personal}
            · this is your personal workspace
          {/if}
        </p>
      </div>
    {/if}
  </section>

  <WorkspaceMembers />

  {#if meta}
    <section class="card p-4">
      <h2 class="mb-3 text-xs font-medium uppercase tracking-wide text-ink-500">
        How your knowledge is processed
      </h2>
      <dl class="grid gap-3 text-sm sm:grid-cols-2">
        <div>
          <dt class="text-xs text-ink-500">Embedding model</dt>
          <dd class="font-mono text-ink-200">{meta.embedding_model}</dd>
        </div>
        <div>
          <dt class="text-xs text-ink-500">Vector dimensions</dt>
          <dd class="font-mono text-ink-200">{meta.embedding_dim}</dd>
        </div>
        <div>
          <dt class="text-xs text-ink-500">Answer model</dt>
          <dd class="font-mono text-ink-200">{meta.llm_model}</dd>
        </div>
        <div>
          <dt class="text-xs text-ink-500">Sources per answer</dt>
          <dd class="font-mono text-ink-200">up to {meta.retrieval_k}</dd>
        </div>
      </dl>
      <p class="mt-3 text-[11px] leading-relaxed text-ink-500">
        These are pinned server-side. Changing the embedding model rebuilds the index, so it is a
        migration rather than a setting.
      </p>
    </section>

    <section class="card p-4">
      <h2 class="mb-2 text-xs font-medium uppercase tracking-wide text-ink-500">Supported files</h2>
      <ul class="space-y-1 text-xs text-ink-300">
        {#each meta.supported_mime_types as mime}
          <li class="font-mono">{mime}</li>
        {/each}
      </ul>
      <p class="mt-2 text-[11px] text-ink-500">
        Scanned PDFs without a text layer are rejected with an explanation rather than silently
        producing an empty document.
      </p>
    </section>
  {/if}
</div>
