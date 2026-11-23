import type { SupabaseClient, Session } from '@supabase/supabase-js';

declare global {
  namespace App {
    interface Locals {
      supabase: SupabaseClient;
      safeGetSession: () => Promise<{ session: Session | null; user: { id: string } | null }>;
    }

    interface PageData {
      session?: Session | null;
    }

    /**
     * The shape every error reaching `+error.svelte` has.
     *
     * `message` is SvelteKit's, and is the only safe field — it is what a
     * `handle` hook or a `load` explicitly chose to expose, never a raw
     * exception string.
     *
     * `code` mirrors the API's error envelope (`{"error":{"code":...}}`) so the
     * error page can show the same machine-readable code the API returned. It is
     * optional because a 404 thrown by the router has no code to give.
     */
    interface Error {
      message: string;
      code?: string;
    }
  }
}

export {};
