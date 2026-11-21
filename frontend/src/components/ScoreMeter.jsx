import { formatScore } from "../lib/format.js";

// The relevance rail: a thin teal meter and the score to two decimals.
export default function ScoreMeter({ score }) {
  const pct = Math.round(Math.min(1, Math.max(0, score)) * 100);
  return (
    <div className="flex w-20 shrink-0 flex-col gap-1" aria-label={`Relevance ${formatScore(score)}`}>
      <span className="text-sm font-semibold tabular-nums text-primary">{formatScore(score)}</span>
      <span aria-hidden="true" className="block h-1 w-full overflow-hidden rounded-full bg-line-soft">
        <span className="block h-full bg-primary" style={{ width: `${pct}%` }} />
      </span>
    </div>
  );
}
