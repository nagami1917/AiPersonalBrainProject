/** Conditional class names.
 *
 * Used instead of Svelte's `class:name={cond}` directives because several
 * Tailwind colours contain a slash (`bg-accent-600/5`), which is not a legal
 * directive name. A single helper also keeps the markup readable.
 */
export function cx(...parts: Array<string | false | null | undefined>): string {
  return parts.filter(Boolean).join(' ');
}
