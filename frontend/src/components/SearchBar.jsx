import { forwardRef } from "react";

const SearchBar = forwardRef(function SearchBar(
  { value, onChange, onArrowDown, loading, resultCount, tookMs },
  ref,
) {
  return (
    <div className="relative">
      <label htmlFor="query" className="block text-sm font-medium text-muted">
        Search records
      </label>
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
        className="mt-1 w-full border-0 border-b-2 border-line bg-transparent py-2 text-xl text-ink placeholder:text-faint focus:border-signal focus:outline-none focus-visible:outline-none sm:text-2xl"
      />
      <div
        aria-hidden="true"
        className={`absolute inset-x-0 bottom-0 h-0.5 ${loading ? "progress-line" : ""}`}
      />
      <p id="search-help" className="mt-2 text-sm text-muted">
        {resultCount != null
          ? `${resultCount} ${resultCount === 1 ? "match" : "matches"}${tookMs != null ? ` in ${tookMs} ms` : ""}`
          : "Results update as you type. Press the down arrow to move into the list."}
      </p>
    </div>
  );
});

export default SearchBar;
