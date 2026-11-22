/** Source links: a note and a document live at different routes. */
import type { SourceKind } from '$lib/api/types';

export function sourceHref(kind: SourceKind, id: string): string {
  return kind === 'note' ? `/notes/${id}` : `/documents/${id}`;
}

export function sourceLabel(kind: SourceKind): string {
  return kind === 'note' ? 'Note' : 'Document';
}
