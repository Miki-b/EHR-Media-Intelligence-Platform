const EXAMPLES = [
  "abnormal chest x-ray",
  "poorly controlled blood sugar",
  "patients discharged after heart failure",
  "elevated HbA1c",
];

// What search reads, labelled with the same type colours the results use.
const SOURCES = [
  { swatch: "bg-doc", label: "Notes and discharge summaries" },
  { swatch: "bg-report", label: "Lab and imaging reports" },
  { swatch: "bg-summary", label: "AI summary for every patient" },
];

function SearchGlyph() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true" className="size-3.5 shrink-0">
      <circle cx="11" cy="11" r="6.5" fill="none" stroke="currentColor" strokeWidth="2.2" />
      <path d="m16 16 4.5 4.5" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" />
    </svg>
  );
}

export function Idle({ onPick }) {
  return (
    <section className="py-8" aria-labelledby="idle-title">
      <h2 id="idle-title" className="text-lg font-medium text-ink">
        Ask a clinical question in plain words.
      </h2>
      <p className="mt-1 max-w-prose text-sm text-muted">
        Search reads the text of notes, lab and imaging reports, and the AI summary for every
        patient. Open a result to see the summary and the records it cites.
      </p>
      <ul className="mt-4 flex flex-wrap gap-x-5 gap-y-2 text-sm text-muted" aria-label="What search reads">
        {SOURCES.map((s) => (
          <li key={s.label} className="flex items-center gap-2">
            <span aria-hidden="true" className={`inline-block size-2.5 rounded-[3px] ${s.swatch}`} />
            {s.label}
          </li>
        ))}
      </ul>
      <p className="mt-6 text-xs font-medium uppercase tracking-wide text-muted">Try</p>
      <ul className="mt-2 flex flex-wrap gap-2" aria-label="Example searches">
        {EXAMPLES.map((q) => (
          <li key={q}>
            <button
              type="button"
              onClick={() => onPick(q)}
              className="inline-flex items-center gap-1.5 rounded-full border border-line bg-panel px-3.5 py-1.5 text-sm text-ink shadow-sm transition hover:-translate-y-px hover:border-primary hover:text-primary hover:shadow-md"
            >
              <SearchGlyph />
              {q}
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}

export function NoResults({ query, filtered, onClearFilters }) {
  return (
    <section className="py-10" role="status">
      <h2 className="text-lg font-medium text-ink">No records match “{query}”.</h2>
      <p className="mt-1 max-w-prose text-sm text-muted">
        {filtered
          ? "The filters may be hiding matches. Clear them, widen the dates, or try different words."
          : "Try different words, or a symptom or test name instead of a diagnosis."}
      </p>
      {filtered && (
        <button
          type="button"
          onClick={onClearFilters}
          className="mt-4 rounded-lg bg-primary px-3.5 py-1.5 text-sm font-medium text-white shadow-sm hover:bg-primary-hover active:bg-primary-hover"
        >
          Clear filters
        </button>
      )}
    </section>
  );
}

export function Failed({ message }) {
  const offline = /not reachable/i.test(message ?? "");
  return (
    <section className="py-10" role="alert">
      <h2 className="text-lg font-medium text-critical">
        {offline ? "The API is not reachable." : "Search failed."}
      </h2>
      <p className="mt-1 max-w-prose text-sm text-muted">
        {offline ? (
          <>
            Start it with <code className="rounded bg-line-soft px-1">make api</code> and this page
            will pick it up on the next keystroke.
          </>
        ) : (
          message
        )}
      </p>
    </section>
  );
}
