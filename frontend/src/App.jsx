import { useCallback, useEffect, useRef, useState } from "react";
import { Failed, Idle, NoResults } from "./components/EmptyState.jsx";
import Filters from "./components/Filters.jsx";
import Header from "./components/Header.jsx";
import PatientDrawer from "./components/PatientDrawer.jsx";
import ResultList from "./components/ResultList.jsx";
import SearchBar from "./components/SearchBar.jsx";
import { useSearch } from "./hooks/useSearch.js";

const NO_FILTERS = { resource_type: "", date_from: "", date_to: "" };

// The search lives in the URL (?q=&resource_type=&date_from=&date_to=) so a
// clinician can share or bookmark it and reload keeps their place.
function readUrl() {
  const p = new URLSearchParams(window.location.search);
  return {
    query: p.get("q") ?? "",
    patient: p.get("patient"),
    filters: {
      resource_type: p.get("resource_type") ?? "",
      date_from: p.get("date_from") ?? "",
      date_to: p.get("date_to") ?? "",
    },
  };
}

function writeUrl(query, filters, patientId) {
  const p = new URLSearchParams();
  if (query.trim()) p.set("q", query.trim());
  for (const [k, v] of Object.entries(filters)) if (v) p.set(k, v);
  if (patientId) p.set("patient", patientId);
  const qs = p.toString();
  window.history.replaceState(null, "", qs ? `?${qs}` : window.location.pathname);
}

export default function App() {
  const [initial] = useState(readUrl);
  const [query, setQuery] = useState(initial.query);
  const [filters, setFilters] = useState(initial.filters);

  // { patientId, resourceId } of the open drawer; resourceId is the row that was clicked
  const [open, setOpen] = useState(initial.patient ? { patientId: initial.patient } : null);

  useEffect(() => writeUrl(query, filters, open?.patientId), [query, filters, open]);
  const inputRef = useRef(null);
  const listRef = useRef(null);

  const { status, results, tookMs, error } = useSearch(query, filters);
  const filtered = Object.values(filters).some(Boolean);
  const showList = results.length > 0 && status !== "error";

  const openPatient = useCallback((patientId, resourceId) => setOpen({ patientId, resourceId }), []);
  const closePatient = useCallback(() => setOpen(null), []);

  const pickExample = (q) => {
    setQuery(q);
    inputRef.current?.focus();
  };

  return (
    <>
      <a
        href="#query"
        className="sr-only focus:not-sr-only focus:absolute focus:left-2 focus:top-2 focus:z-50 focus:rounded focus:bg-panel focus:px-3 focus:py-2 focus:text-sm focus:text-primary"
      >
        Skip to search
      </a>
      <Header />
      <main className="mx-auto max-w-6xl px-4 pb-16 pt-8 sm:px-6">
        <SearchBar
          ref={inputRef}
          value={query}
          onChange={setQuery}
          onArrowDown={() => listRef.current?.focusFirst()}
          loading={status === "loading"}
          resultCount={status === "success" ? results.length : null}
          tookMs={status === "success" ? tookMs : null}
        />
        <Filters filters={filters} onChange={setFilters} />

        <section className="mt-6" aria-live="polite" aria-atomic="false">
          {status === "idle" && <Idle onPick={pickExample} />}
          {status === "error" && <Failed message={error} />}
          {status === "success" && results.length === 0 && (
            <NoResults query={query.trim()} filtered={filtered} onClearFilters={() => setFilters(NO_FILTERS)} />
          )}
          {showList && (
            <ResultList
              ref={listRef}
              results={results}
              onOpen={openPatient}
              busy={status === "loading"}
            />
          )}
        </section>
      </main>

      {open && (
        <PatientDrawer patientId={open.patientId} highlight={open.resourceId} onClose={closePatient} />
      )}
    </>
  );
}
