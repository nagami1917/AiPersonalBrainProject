<script lang="ts">
  /**
   * The persistent left navigation.
   *
   * Extracted from the layout so the layout is about *gating and composition*
   * (who may see this, what wraps the page) and this file is about navigation
   * (what exists, which item is current). They change for different reasons.
   *
   * Collapsed on narrow screens rather than hidden behind a drawer: a drawer
   * needs focus trapping and a backdrop to be correct, and this app has no
   * mobile navigation story yet. `hidden lg:flex` is honest about that.
   */
  import { page } from '$app/state';
  import { session, signOut } from '$lib/stores/session';
  import { cx } from '$lib/utils/cx';
  import WorkspaceSwitcher from './WorkspaceSwitcher.svelte';

  const nav = [
    { href: '/dashboard', label: 'Welcome', icon: '◈' },
    { href: '/assistant', label: 'Assistant', icon: '✦' },
    { href: '/documents', label: 'Documents', icon: '▤' },
    { href: '/notes', label: 'Notes', icon: '✎' },
    { href: '/collections', label: 'Collections', icon: '⌸' },
    { href: '/graph', label: 'Graph', icon: '◉' },
    { href: '/settings', label: 'Settings', icon: '⚙' }
  ];

  const current = $derived(page.url.pathname);
</script>

<aside class="hidden w-56 shrink-0 border-r border-ink-800 lg:flex lg:flex-col">
  <div class="px-4 py-5">
    <a href="/dashboard" class="text-lg font-semibold tracking-tight text-ink-50">NEXUS</a>
  </div>

  <div class="px-2 pb-3">
    <WorkspaceSwitcher />
  </div>

  <nav class="flex-1 space-y-0.5 px-2" aria-label="Main">
    {#each nav as item (item.href)}
      <a
        href={item.href}
        aria-current={current.startsWith(item.href) ? 'page' : undefined}
        class={cx(
          'flex items-center gap-2.5 rounded-lg px-2.5 py-2 text-sm transition-colors',
          current.startsWith(item.href)
            ? 'bg-ink-800 text-ink-50'
            : 'text-ink-300 hover:bg-ink-800/60'
        )}
      >
        <span class="text-xs opacity-70" aria-hidden="true">{item.icon}</span>
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
