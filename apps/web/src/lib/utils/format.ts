export function formatBytes(bytes: number | null): string {
  if (!bytes) return '—';
  const units = ['B', 'KB', 'MB', 'GB'];
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value.toFixed(value < 10 && unit > 0 ? 1 : 0)} ${units[unit]}`;
}

export function relativeTime(iso: string): string {
  const then = new Date(iso).getTime();
  const seconds = Math.round((Date.now() - then) / 1000);

  if (seconds < 60) return 'just now';
  if (seconds < 3600) return `${Math.round(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.round(seconds / 3600)}h ago`;
  if (seconds < 604800) return `${Math.round(seconds / 86400)}d ago`;
  return new Date(iso).toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
}

/**
 * Split answer text into plain runs and citation markers so the UI can render
 * a real chip per source instead of leaving "[1]" as literal text.
 */
export type AnswerSegment =
  | { kind: 'text'; value: string }
  | { kind: 'marker'; markers: number[] };

export function segmentAnswer(text: string): AnswerSegment[] {
  const segments: AnswerSegment[] = [];
  const pattern = /\[(\d{1,3})\]/g;
  let lastIndex = 0;
  let match: RegExpExecArray | null;

  while ((match = pattern.exec(text)) !== null) {
    if (match.index > lastIndex) {
      segments.push({ kind: 'text', value: text.slice(lastIndex, match.index) });
    }
    const marker = Number(match[1]);
    const previous = segments[segments.length - 1];
    if (previous?.kind === 'marker') {
      previous.markers.push(marker);
    } else {
      segments.push({ kind: 'marker', markers: [marker] });
    }
    lastIndex = match.index + match[0].length;
  }

  if (lastIndex < text.length) {
    segments.push({ kind: 'text', value: text.slice(lastIndex) });
  }
  return segments;
}
