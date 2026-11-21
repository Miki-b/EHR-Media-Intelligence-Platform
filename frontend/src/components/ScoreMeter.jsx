import { formatScore } from "../lib/format.js";

const R = 17;
const CIRCUMFERENCE = 2 * Math.PI * R;

// Relevance as a small ring gauge: the arc fills with the score, the number sits inside.
export default function ScoreMeter({ score }) {
  const value = Math.min(1, Math.max(0, score));
  return (
    <div className="relative size-11 shrink-0" aria-label={`Relevance ${formatScore(score)}`}>
      <svg viewBox="0 0 40 40" className="size-full -rotate-90" aria-hidden="true">
        <circle cx="20" cy="20" r={R} fill="none" strokeWidth="3.5" className="stroke-line-soft" />
        <circle
          cx="20"
          cy="20"
          r={R}
          fill="none"
          strokeWidth="3.5"
          strokeLinecap="round"
          strokeDasharray={CIRCUMFERENCE}
          strokeDashoffset={CIRCUMFERENCE * (1 - value)}
          className="stroke-primary transition-[stroke-dashoffset] duration-500"
        />
      </svg>
      <span
        aria-hidden="true"
        className="absolute inset-0 grid place-items-center text-[0.7rem] font-semibold tabular-nums text-primary"
      >
        {formatScore(score)}
      </span>
    </div>
  );
}
