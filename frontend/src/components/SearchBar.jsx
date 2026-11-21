import { forwardRef } from "react";

function SearchIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true" className="size-6 shrink-0 text-primary">
      <circle cx="11" cy="11" r="6.5" fill="none" stroke="currentColor" strokeWidth="2" />
      <path d="m16 16 4.5 4.5" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
    </svg>
  );
}

function Chip({ children }) {
  return (
    <span className="inline-flex items-center rounded-full bg-primary-tint px-2.5 py-0.5 text-xs font-medium text-primary">
      {children}
    </span>
  );
}

const SearchBar = forwardRef(function SearchBar(
  { value, onChange, onArrowDown, loading, resultCount, tookMs },
  ref,
) {
  return (
    <div>
      <label htmlFor="query" className="block text-sm font-medium text-muted">
        Search records
      </label>
      <div className="relative mt-2 flex items-center gap-3 overflow-hidden rounded-2xl border border-line bg-panel px-4 shadow-sm transition focus-within:border-primary focus-within:ring-4 focus-within:ring-primary/15 sm:px-5">
        <SearchIcon />
        <input
          ref={ref}
          id="query"
          type="search"
          autoComplete="off"
          spellCheck={false}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "ArrowDown") {
              e.preventDefault();
              onArrowDown?.();
            }
          }}
          placeholder="Describe what you are looking for, like elevated HbA1c"
          aria-describedby="search-help"
          aria-controls="results"
          className="min-w-0 flex-1 bg-transparent py-4 text-lg text-ink placeholder:text-faint focus:outline-none focus-visible:outline-none sm:text-xl"
        />
        <kbd
          aria-hidden="true"
          className="hidden rounded-md border border-line bg-paper px-2 py-0.5 font-sans text-xs text-muted sm:inline"
        >
          /
        </kbd>
        <div
          aria-hidden="true"
          className={`absolute inset-x-0 bottom-0 h-0.5 ${loading ? "progress-line" : ""}`}
        />
      </div>
      <p id="search-help" className="mt-2.5 flex flex-wrap items-center gap-2 text-sm text-muted">
        {resultCount != null ? (
          <>
            <Chip>
              {resultCount} {resultCount === 1 ? "match" : "matches"}
            </Chip>
            {tookMs != null && <Chip>{tookMs} ms</Chip>}
          </>
        ) : (
          "Results update as you type. Press the down arrow to move into the list."
        )}
      </p>
    </div>
  );
});

export default SearchBar;
