// Wrap the words of the current query in <mark> so a clinician sees why a record matched.
// Only words of 3+ letters count (skips "of", "a"); matching is case-insensitive, anchored
// at word starts, and marks the whole word. Text is never rewritten, only wrapped.

const MIN_TERM = 3;

function terms(query) {
  const words = (query ?? "").toLowerCase().match(/[\p{L}\p{N}]+/gu) ?? [];
  return [...new Set(words.filter((w) => w.length >= MIN_TERM))];
}

const escape = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

export function Highlight({ text, query }) {
  const found = terms(query);
  if (!text || found.length === 0) return text ?? null;
  // A term marks from a word start to the end of that word ("fail" marks all of "failure").
  const pattern = new RegExp(
    `(?<![\\p{L}\\p{N}])((?:${found.map(escape).join("|")})[\\p{L}\\p{N}]*)`,
    "giu",
  );
  const parts = text.split(pattern);
  return parts.map((part, i) =>
    i % 2 === 1 ? (
      <mark key={i} className="rounded-[3px] bg-primary-tint font-medium text-ink">
        {part}
      </mark>
    ) : (
      part
    ),
  );
}
