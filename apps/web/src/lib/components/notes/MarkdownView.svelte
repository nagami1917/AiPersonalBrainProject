<script lang="ts">
  /**
   * Renders parsed Markdown as real Svelte elements.
   *
   * There is no `{@html}` here, and that is the point: `parseMarkdown` returns
   * data, this component maps it to elements, and text reaches the DOM as text.
   * A note body cannot inject markup because nothing in this path is markup.
   */
  import { parseMarkdown, type InlineSpan } from '$lib/utils/markdown';

  let {
    source,
    wikiLinks = new Map<string, string>()
  }: {
    source: string;
    /** lowercased title → note id, for links that resolve. Missing = unwritten. */
    wikiLinks?: Map<string, string>;
  } = $props();

  const blocks = $derived(parseMarkdown(source));
</script>

{#snippet inline(spans: InlineSpan[])}
  {#each spans as span}
    {#if span.kind === 'text'}
      {span.value}
    {:else if span.kind === 'bold'}
      <strong class="font-semibold text-ink-100">{span.value}</strong>
    {:else if span.kind === 'italic'}
      <em>{span.value}</em>
    {:else if span.kind === 'code'}
      <code class="rounded bg-ink-800 px-1 py-0.5 font-mono text-[0.85em] text-accent-400"
        >{span.value}</code
      >
    {:else if span.kind === 'link'}
      <a
        href={span.href}
        class="text-accent-400 underline decoration-accent-600/40 hover:decoration-accent-400"
        rel="noreferrer noopener"
        target={span.href.startsWith('http') ? '_blank' : undefined}>{span.value}</a
      >
    {:else if span.kind === 'wikilink'}
      {#if wikiLinks.has(span.target.toLowerCase())}
        <a
          href="/notes/{wikiLinks.get(span.target.toLowerCase())}"
          class="rounded bg-accent-500/10 px-1 text-accent-400 hover:bg-accent-500/20"
          >{span.label}</a
        >
      {:else}
        <!-- An unwritten target stays visible but unclickable: it is the user's
             own promise, not a broken link to hide. -->
        <span
          class="rounded border border-dashed border-ink-700 px-1 text-ink-400"
          title="No note with this title yet"
          >{span.label}</span
        >
      {/if}
    {/if}
  {/each}
{/snippet}

<article class="space-y-3 text-[15px] leading-relaxed text-ink-200">
  {#each blocks as block}
    {#if block.kind === 'heading'}
      {#if block.level === 1}
        <h1 class="mt-2 text-xl font-semibold tracking-tight text-ink-50">
          {@render inline(block.spans)}
        </h1>
      {:else if block.level === 2}
        <h2 class="mt-4 text-lg font-semibold tracking-tight text-ink-50">
          {@render inline(block.spans)}
        </h2>
      {:else}
        <h3 class="mt-3 text-sm font-semibold uppercase tracking-wide text-ink-300">
          {@render inline(block.spans)}
        </h3>
      {/if}
    {:else if block.kind === 'paragraph'}
      <p>{@render inline(block.spans)}</p>
    {:else if block.kind === 'code'}
      <pre
        class="overflow-x-auto rounded-lg border border-ink-800 bg-ink-950 px-3 py-2"><code
          class="font-mono text-[13px] text-ink-200">{block.value}</code
        ></pre>
    {:else if block.kind === 'quote'}
      <blockquote class="border-l-2 border-accent-600/50 pl-3 text-ink-300 italic">
        {@render inline(block.spans)}
      </blockquote>
    {:else if block.kind === 'list'}
      {#if block.ordered}
        <ol class="ml-5 list-decimal space-y-1">
          {#each block.items as item}
            <li>{@render inline(item)}</li>
          {/each}
        </ol>
      {:else}
        <ul class="ml-5 list-disc space-y-1">
          {#each block.items as item}
            <li>{@render inline(item)}</li>
          {/each}
        </ul>
      {/if}
    {:else if block.kind === 'rule'}
      <hr class="border-ink-800" />
    {/if}
  {/each}
</article>
