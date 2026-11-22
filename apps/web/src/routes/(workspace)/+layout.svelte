<script lang="ts">
  import { goto } from '$app/navigation';
  import { page } from '$app/state';
  import { session, sessionReady, signOut } from '$lib/stores/session';
  import { loadWorkspaces } from '$lib/stores/workspace';
  import WorkspaceSwitcher from '$lib/components/WorkspaceSwitcher.svelte';
  import { cx } from '$lib/utils/cx';

  let { children } = $props();

  // Client-side gate: FastAPI re-verifies every request, so this is UX only,
  // never the security boundary.
  $effect(() => {
    if ($sessionReady && !$session) {
      goto('/login');
    }
  });

  // The workspace list is loaded once per session, not per page: every page then
  // builds its API paths from the same active workspace.
  $effect(() => {
    if ($session) void loadWorkspaces();
  });

  const nav = [
    { href: '/dashboard', label: 'Dashboard', icon: '◈' },
    { href: '/assistant', label: 'Assistant', icon: '✦' },
    { href: '/documents', label: 'Documents', icon: '▤' },
    { href: '/notes', label: 'Notes', icon: '✎' },
    { href: '/collections', label: 'Collections', icon: '⌸' },
    { href: '/graph', label: 'Graph', icon: '◉' },
    { href: '/settings', label: 'Settings', icon: '⚙' }
  ];

  const current = $derived(page.url.pathname);
</script>

<div class="flex min-h-screen">
  <aside class="hidden w-56 shrink-0 border-r border-ink-800 lg:flex lg:flex-col">
    <div class="px-4 py-5">
      <a href="/dashboard" class="text-lg font-semibold tracking-tight text-ink-50">NEXUS</a>
    </div>

    <div class="px-2 pb-3">
      <WorkspaceSwitcher />
    </div>

    <nav class="flex-1 space-y-0.5 px-2">
      {#each nav as item}
        <a
          href={item.href}
          class={cx(
            'flex items-center gap-2.5 rounded-lg px-2.5 py-2 text-sm transition-colors',
            current.startsWith(item.href)
              ? 'bg-ink-800 text-ink-50'
              : 'text-ink-300 hover:bg-ink-800/60'
          )}
        >
          <span class="text-xs opacity-70">{item.icon}</span>
          {item.label}
        </a>
      {/each}
    </nav>

    <div class="border-t border-ink-800 p-3">
      <p class="truncate px-1 text-xs text-ink-500">{$session?.user.email}</p>
      <button class="btn-ghost mt-1 w-full justify-start px-1 text-xs" onclick={signOut}>
        Sign out
      </button>
    </div>
  </aside>

  <main class="min-w-0 flex-1">
    <div class="mx-auto max-w-5xl px-5 py-8">
      {@render children()}
    </div>
  </main>
</div>
