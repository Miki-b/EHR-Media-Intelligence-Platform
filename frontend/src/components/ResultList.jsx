import { forwardRef, useImperativeHandle, useRef } from "react";
import { formatDate } from "../lib/format.js";
import ScoreMeter from "./ScoreMeter.jsx";
import TypeTag from "./TypeTag.jsx";

/**
 * Results as a worklist. Each row is a button; arrow keys, Home and End move
 * between rows, Enter or Space opens the patient. The parent can call
 * focusFirst() when the user presses the down arrow in the search box.
 */
const ResultList = forwardRef(function ResultList({ results, onOpen, busy }, ref) {
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
      className={`divide-y divide-line-soft border-y border-line transition-opacity ${busy ? "opacity-60" : ""}`}
    >
      {results.map((r, i) => (
        <li key={`${r.resource_id}-${r.patient_id}`}>
          <button
            type="button"
            ref={(el) => (rowRefs.current[i] = el)}
            onClick={() => onOpen(r.patient_id, r.resource_id)}
            onKeyDown={(e) => onKeyDown(e, i)}
            aria-label={`${r.patient_name}, MRN ${r.mrn}, ${r.resource_type} dated ${formatDate(r.date)}, relevance ${r.score.toFixed(2)}. Open patient.`}
            className="grid w-full grid-cols-[5rem_1fr] gap-x-4 gap-y-1 px-2 py-3 text-left hover:bg-primary-tint focus-visible:bg-primary-tint sm:px-3"
          >
            <ScoreMeter score={r.score} />
            <div className="min-w-0">
              <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
                <span className="font-semibold text-ink">{r.patient_name}</span>
                <span className="text-sm tabular-nums text-muted">{r.mrn}</span>
                <span className="ml-auto flex items-center gap-3 text-sm text-muted">
                  <TypeTag resourceType={r.resource_type} />
                  <time dateTime={r.date ?? undefined} className="tabular-nums">
                    {formatDate(r.date)}
                  </time>
                </span>
              </div>
              <p className="mt-1 line-clamp-2 text-sm text-ink">{r.snippet}</p>
              {r.summary_snippet && r.resource_type !== "Summary" && (
                <p className="mt-1 line-clamp-1 font-serif text-sm text-muted">
                  <span className="text-summary">Summary </span>
                  {r.summary_snippet}
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
