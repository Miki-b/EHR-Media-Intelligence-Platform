import { forwardRef, useImperativeHandle, useRef } from "react";
import { formatDate } from "../lib/format.js";
import { Highlight } from "../lib/highlight.jsx";
import ScoreMeter from "./ScoreMeter.jsx";
import TypeTag from "./TypeTag.jsx";

function initials(name) {
  const parts = (name ?? "").split(/\s+/).filter(Boolean);
  return ((parts[0]?.[0] ?? "") + (parts.length > 1 ? parts.at(-1)[0] : "")).toUpperCase() || "?";
}

/**
 * Results as a worklist of cards. Each card is a button; arrow keys, Home and End move
 * between cards, Enter or Space opens the patient. The parent can call focusFirst()
 * when the user presses the down arrow in the search box. Query words are highlighted
 * in the snippets so the reason for a match is visible at a glance.
 */
const ResultList = forwardRef(function ResultList({ results, query, onOpen, busy }, ref) {
  const rowRefs = useRef([]);

  useImperativeHandle(ref, () => ({
    focusFirst: () => rowRefs.current[0]?.focus(),
  }));

  const onKeyDown = (e, i) => {
    const last = results.length - 1;
    const go = (j) => {
      e.preventDefault();
      rowRefs.current[j]?.focus();
    };
    if (e.key === "ArrowDown") go(Math.min(last, i + 1));
    else if (e.key === "ArrowUp") go(Math.max(0, i - 1));
    else if (e.key === "Home") go(0);
    else if (e.key === "End") go(last);
  };

  return (
    <ol
      id="results"
      aria-label="Search results"
      aria-busy={busy || undefined}
      className={`flex flex-col gap-2.5 transition-opacity ${busy ? "opacity-60" : ""}`}
    >
      {results.map((r, i) => (
        <li
          key={`${r.resource_id}-${r.patient_id}`}
          className="fade-up"
          style={{ animationDelay: `${Math.min(i, 8) * 45}ms` }}
        >
          <button
            type="button"
            ref={(el) => (rowRefs.current[i] = el)}
            onClick={() => onOpen(r.patient_id, r.resource_id)}
            onKeyDown={(e) => onKeyDown(e, i)}
            aria-label={`${r.patient_name}, MRN ${r.mrn}, ${r.resource_type} dated ${formatDate(r.date)}, relevance ${r.score.toFixed(2)}. Open patient.`}
            className="group grid w-full grid-cols-[auto_1fr] gap-x-4 rounded-xl border border-line bg-panel px-4 py-3.5 text-left shadow-sm transition hover:-translate-y-px hover:border-primary/40 hover:shadow-md focus-visible:border-primary sm:px-5"
          >
            <ScoreMeter score={r.score} />
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
                <span
                  aria-hidden="true"
                  className="grid size-7 shrink-0 place-items-center rounded-full bg-primary-tint text-xs font-semibold text-primary"
                >
                  {initials(r.patient_name)}
                </span>
                <span className="font-semibold text-ink group-hover:text-primary">{r.patient_name}</span>
                <span className="text-sm tabular-nums text-muted">{r.mrn}</span>
                <span className="ml-auto flex items-center gap-3 text-sm text-muted">
                  <TypeTag resourceType={r.resource_type} />
                  <time dateTime={r.date ?? undefined} className="tabular-nums">
                    {formatDate(r.date)}
                  </time>
                </span>
              </div>
              <p className="mt-2 line-clamp-2 text-sm text-ink">
                <Highlight text={r.snippet} query={query} />
              </p>
              {r.summary_snippet && r.resource_type !== "Summary" && (
                <p className="mt-1.5 line-clamp-1 border-l-2 border-summary/40 pl-2 font-serif text-sm text-muted">
                  <span className="text-summary">Summary </span>
                  <Highlight text={r.summary_snippet} query={query} />
                </p>
              )}
            </div>
          </button>
        </li>
      ))}
    </ol>
  );
});

export default ResultList;
