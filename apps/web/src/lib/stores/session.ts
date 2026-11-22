/** Session store: one subscription, one source of truth for auth state. */
import { writable, derived } from 'svelte/store';
import type { Session } from '@supabase/supabase-js';
import { getSupabaseSync } from '$lib/api/supabase';

export const session = writable<Session | null>(null);
export const sessionReady = writable(false);
export const user = derived(session, ($session) => $session?.user ?? null);

let subscribed = false;

export function initSession(): void {
  if (subscribed || typeof window === 'undefined') return;
  subscribed = true;

  const supabase = getSupabaseSync();
  supabase.auth.getSession().then(({ data }) => {
    session.set(data.session);
    sessionReady.set(true);
  });

  supabase.auth.onAuthStateChange((_event, next) => {
    session.set(next);
    sessionReady.set(true);
  });
}

export async function signOut(): Promise<void> {
  await getSupabaseSync().auth.signOut();
  session.set(null);
}
