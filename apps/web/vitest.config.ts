import { fileURLToPath } from 'node:url';

import { defineConfig } from 'vitest/config';

/**
 * Unit tests for pure logic only (Markdown parsing, autosave scheduling, the
 * API proxy's routing decisions).
 *
 * These modules have no Svelte runtime dependency, which is why they can be
 * tested without a browser or a component harness — and why they were written
 * that way: the things most likely to be subtly wrong here are link
 * extraction, save ordering, and which upstream a request is forwarded to, and
 * all of them are cheap to pin down as data.
 *
 * The `$lib` alias mirrors SvelteKit's. Vitest does not read the `kit.alias`
 * from `svelte.config.js`, so a module importing `$lib/...` fails to resolve
 * until it is declared here — which is exactly what happened when the proxy
 * test was added, since the three original test files only imported relative
 * paths.
 */
export default defineConfig({
  resolve: {
    alias: {
      $lib: fileURLToPath(new URL('./src/lib', import.meta.url))
    }
  },
  test: {
    include: ['src/**/*.test.ts'],
    environment: 'node'
  }
});
