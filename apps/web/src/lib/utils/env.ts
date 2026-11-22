/**
 * Public configuration, read once and validated.
 *
 * Only PUBLIC_* values exist here. The service-role key and AI keys live on the
 * backend and must never appear in this file or in Vercel's frontend env
 * (ARCHITECTURE.md §3).
 */
import { env as publicEnv } from '$env/dynamic/public';

function required(name: string, value: string | undefined): string {
  if (!value) {
    throw new Error(`Missing public environment variable: ${name}`);
  }
  return value;
}

export const env = {
  supabaseUrl: required('PUBLIC_SUPABASE_URL', publicEnv.PUBLIC_SUPABASE_URL),
  supabaseAnonKey: required('PUBLIC_SUPABASE_ANON_KEY', publicEnv.PUBLIC_SUPABASE_ANON_KEY),
  apiBaseUrl: required('PUBLIC_API_BASE_URL', publicEnv.PUBLIC_API_BASE_URL).replace(/\/$/, '')
};
