const EXAMPLES = [
  "abnormal chest x-ray",
  "poorly controlled blood sugar",
  "patients discharged after heart failure",
  "elevated HbA1c",
];

export function Idle({ onPick }) {
  return (
    <section className="py-10" aria-labelledby="idle-title">
      <h2 id="idle-title" className="text-lg font-medium text-ink">
        Ask a clinical question in plain words.
      </h2>
      <p className="mt-1 max-w-prose text-sm text-muted">
        Search reads the text of notes, lab and imaging reports, and the AI summary for every
        patient. Open a result to see the summary and the records it cites.
      </p>
      <ul className="mt-5 flex flex-wrap gap-2" aria-label="Example searches">
        {EXAMPLES.map((q) => (
          <li key={q}>
            <button
              type="button"
              onClick={() => onPick(q)}
              className="rounded-full border border-line bg-panel px-3 py-1 text-sm text-ink hover:border-primary hover:text-primary"
            >
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
          className="mt-4 rounded bg-primary px-3 py-1.5 text-sm font-medium text-white hover:bg-primary-hover active:bg-primary-hover"
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
