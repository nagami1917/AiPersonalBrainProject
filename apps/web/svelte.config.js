import adapter from '@sveltejs/adapter-vercel';
import { vitePreprocess } from '@sveltejs/vite-plugin-svelte';

/** @type {import('@sveltejs/kit').Config} */
const config = {
  preprocess: vitePreprocess(),
  kit: {
    // Pinned so the deployed runtime cannot drift with Vercel's Node default.
    // Must stay a runtime Vercel still offers: `nodejs20.x` was discontinued and
    // `vercel deploy` failed with NOW_SANDBOX_WORKER_RUNTIME_INVALID even though
    // the build itself succeeded. Matches CI (.github/workflows/ci.yml → node 22).
    adapter: adapter({ runtime: 'nodejs22.x' }),
    alias: {
      $lib: './src/lib'
    }
  }
};

export default config;
