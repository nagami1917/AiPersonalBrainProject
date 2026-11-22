/**
 * Browser Supabase client (singleton).
 *
 * Auth and signed uploads only. Anything that ranks, generates, or touches
 * another table goes through FastAPI, because the anon key plus RLS is the
 * whole of the browser's authority.
 */
import { createBrowserClient } from '@supabase/ssr';
import type { SupabaseClient } from '@supabase/supabase-js';
import { env } from '$lib/utils/env';

let client: SupabaseClient | null = null;

export function getSupabaseSync(): SupabaseClient {
  if (!client) {
    client = createBrowserClient(env.supabaseUrl, env.supabaseAnonKey);
  }
  return client;
}

export async function getSupabase(): Promise<SupabaseClient> {
  return getSupabaseSync();
}

/** Upload directly to Storage with the user's session; bytes never touch FastAPI. */
export async function uploadFile(storagePath: string, file: File): Promise<void> {
  const supabase = getSupabaseSync();
  const { error } = await supabase.storage.from('nexus-documents').upload(storagePath, file, {
    contentType: file.type,
    upsert: true
  });
  if (error) throw error;
}
