import { useEffect, useRef, useState } from "react";
import { search } from "../api.js";

const DEBOUNCE_MS = 250;
const MIN_QUERY = 2;

/**
 * Debounced, cancellable search. Keeps the last good results on screen while a
 * new request is in flight, and ignores responses that arrive out of order.
 *
 * status: "idle" (nothing typed yet) | "loading" | "success" | "error"
 */
export function useSearch(query, filters, topK = 5) {
  const [state, setState] = useState({ status: "idle", results: [], tookMs: null, error: null });
  const requestId = useRef(0);

  const trimmed = query.trim();
  const { resource_type, date_from, date_to } = filters;

  useEffect(() => {
    if (trimmed.length < MIN_QUERY) {
      requestId.current += 1;
      setState({ status: "idle", results: [], tookMs: null, error: null });
      return undefined;
    }
    if (date_from && date_to && date_from > date_to) {
      setState((s) => ({ ...s, status: "error", error: "The start date is after the end date." }));
      return undefined;
    }

    const id = ++requestId.current;
    const controller = new AbortController();
    setState((s) => ({ ...s, status: "loading", error: null }));

    const timer = setTimeout(() => {
      search(trimmed, { resource_type, date_from, date_to }, { signal: controller.signal, topK })
        .then((data) => {
          if (id !== requestId.current) return;
          setState({ status: "success", results: data.results, tookMs: data.took_ms, error: null });
        })
        .catch((err) => {
          if (err.name === "AbortError" || id !== requestId.current) return;
          setState((s) => ({ ...s, status: "error", error: err.message }));
        });
    }, DEBOUNCE_MS);

    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [trimmed, resource_type, date_from, date_to, topK]);

  return state;
}
