<script lang="ts">
  import { goto } from '$app/navigation';
  import { getSupabaseSync } from '$lib/api/supabase';

  let mode = $state<'signin' | 'signup'>('signin');
  let email = $state('');
  let password = $state('');
  let busy = $state(false);
  let error = $state<string | null>(null);
  let notice = $state<string | null>(null);

  async function submit(event: SubmitEvent) {
    event.preventDefault();
    busy = true;
    error = null;
    notice = null;

    const supabase = getSupabaseSync();
    const { error: authError } =
      mode === 'signin'
        ? await supabase.auth.signInWithPassword({ email, password })
        : await supabase.auth.signUp({ email, password });

    busy = false;

    if (authError) {
      error = authError.message;
      return;
    }

    if (mode === 'signup') {
      notice = 'Check your email to confirm your account, then sign in.';
      mode = 'signin';
      return;
    }

    await goto('/dashboard');
  }
</script>

<svelte:head><title>Sign in · NEXUS</title></svelte:head>

<main class="flex min-h-screen items-center justify-center px-4">
  <div class="w-full max-w-sm">
    <div class="mb-8 text-center">
      <h1 class="text-2xl font-semibold tracking-tight text-ink-50">NEXUS</h1>
      <p class="mt-1 text-sm text-ink-400">Your knowledge, with sources.</p>
    </div>

    <form class="card space-y-4 p-6" onsubmit={submit}>
      <div>
        <label class="label" for="email">Email</label>
        <input id="email" class="input" type="email" bind:value={email} required autocomplete="email" />
      </div>

      <div>
        <label class="label" for="password">Password</label>
        <input
          id="password"
          class="input"
          type="password"
          bind:value={password}
          required
          minlength={8}
          autocomplete={mode === 'signin' ? 'current-password' : 'new-password'}
        />
      </div>

      {#if error}
        <p class="rounded-lg bg-red-950/40 px-3 py-2 text-sm text-red-300" role="alert">{error}</p>
      {/if}
      {#if notice}
        <p class="rounded-lg bg-emerald-950/40 px-3 py-2 text-sm text-emerald-300">{notice}</p>
      {/if}

      <button class="btn-primary w-full" type="submit" disabled={busy}>
        {busy ? 'Working…' : mode === 'signin' ? 'Sign in' : 'Create account'}
      </button>

      <button
        type="button"
        class="btn-ghost w-full text-xs"
        onclick={() => {
          mode = mode === 'signin' ? 'signup' : 'signin';
          error = null;
          notice = null;
        }}
      >
        {mode === 'signin' ? 'Need an account? Sign up' : 'Have an account? Sign in'}
      </button>
    </form>

    <p class="mt-6 text-center text-xs leading-relaxed text-ink-500">
      NEXUS answers only from documents you have added, and cites every claim.
    </p>
  </div>
</main>
