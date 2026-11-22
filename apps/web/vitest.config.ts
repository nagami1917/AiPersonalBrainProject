import { defineConfig } from 'vitest/config';

/**
 * Unit tests for pure logic only (Markdown parsing, autosave scheduling).
 *
 * These modules have no Svelte runtime dependency, which is why they can be
 * tested without a browser or a component harness — and why they were written
 * that way: the two things most likely to be subtly wrong here are link
 * extraction and save ordering, and both are cheap to pin down as data.
 */
export default defineConfig({
  test: {
    include: ['src/**/*.test.ts'],
    environment: 'node'
  }
});
