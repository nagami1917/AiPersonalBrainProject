import { sveltekit } from '@sveltejs/kit/vite';
import { defineConfig } from 'vite';

export default defineConfig({
  plugins: [sveltekit()],
  server: {
    host: '0.0.0.0',
    port: 5173,
    strictPort: true,
    // Served behind a dynamic preview proxy host, so no host allowlist.
    // Restricting `allowedHosts` here breaks the live preview in hosted envs.
    allowedHosts: true,
    fs: { allow: ['..'] }
  }
});
