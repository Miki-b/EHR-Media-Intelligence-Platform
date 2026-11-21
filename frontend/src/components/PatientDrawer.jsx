import { useEffect, useRef, useState } from "react";
import { getPatient } from "../api.js";
import { ageFrom, formatDate, genderLabel } from "../lib/format.js";
import TypeTag from "./TypeTag.jsx";

const FOCUSABLE = 'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])';

/**
 * Right-hand panel (full screen on phones) with the AI summary and every
 * linked FHIR resource. `highlight` is the resource id the user clicked on.
 */
export default function PatientDrawer({ patientId, highlight, onClose }) {
  const [state, setState] = useState({ status: "loading", detail: null, error: null });
  const panelRef = useRef(null);
  const closeRef = useRef(null);

  useEffect(() => {
    const controller = new AbortController();
    setState({ status: "loading", detail: null, error: null });
    getPatient(patientId, { signal: controller.signal })
      .then((detail) => setState({ status: "success", detail, error: null }))
      .catch((err) => {
        if (err.name !== "AbortError") setState({ status: "error", detail: null, error: err.message });
      });
    return () => controller.abort();
  }, [patientId]);

  // Focus management: focus the close button on open, trap Tab inside, Esc closes.
  useEffect(() => {
    const previous = document.activeElement;
    closeRef.current?.focus();
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";

    const onKey = (e) => {
      if (e.key === "Escape") {
        e.preventDefault();
        onClose();
        return;
      }
      if (e.key !== "Tab" || !panelRef.current) return;
      const nodes = [...panelRef.current.querySelectorAll(FOCUSABLE)].filter((n) => !n.disabled);
      if (!nodes.length) return;
      const first = nodes[0];
      const last = nodes[nodes.length - 1];
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = prevOverflow;
      previous?.focus?.();
    };
  }, [onClose]);

  // Scroll the clicked resource into view once the detail has loaded.
  useEffect(() => {
    if (state.status !== "success" || !highlight) return;
    const el = panelRef.current?.querySelector(`[data-resource-id="${CSS.escape(highlight)}"]`);
    el?.scrollIntoView({ block: "center" });
  }, [state.status, highlight]);

  const { detail } = state;
  const title = detail ? detail.patient_name : "Patient";

  return (
    <div className="fixed inset-0 z-40 flex justify-end">
      <button
        type="button"
        aria-label="Close patient details"
        onClick={onClose}
        className="absolute inset-0 bg-ink/30"
        tabIndex={-1}
      />
      <aside
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="drawer-title"
        className="scrollbar-thin relative flex h-full w-full max-w-xl flex-col overflow-y-auto bg-panel shadow-[-8px_0_24px_rgba(23,38,43,0.12)]"
      >
        <header className="sticky top-0 z-10 flex items-start justify-between gap-4 border-b border-line bg-panel px-5 py-4 sm:px-6">
          <div className="min-w-0">
            <h2 id="drawer-title" className="truncate text-xl font-semibold text-ink">
              {title}
            </h2>
            {detail && (
              <p className="mt-0.5 text-sm text-muted">
                <span className="tabular-nums">{detail.mrn}</span>
                {" · "}
                {detail.dob ? (
                  <>
                    Born {formatDate(detail.dob)}
                    {ageFrom(detail.dob) != null && ` (${ageFrom(detail.dob)})`}
                  </>
                ) : (
                  "Date of birth not recorded"
                )}
                {" · "}
                {genderLabel(detail.gender)}
              </p>
            )}
          </div>
          <button
            ref={closeRef}
            type="button"
            onClick={onClose}
            className="shrink-0 rounded border border-line px-2.5 py-1 text-sm text-muted hover:border-ink hover:text-ink"
          >
            Close
          </button>
        </header>

        <div className="flex-1 px-5 py-5 sm:px-6">
          {state.status === "loading" && (
            <p role="status" className="text-sm text-muted">
              Loading patient…
            </p>
          )}
          {state.status === "error" && (
            <p role="alert" className="text-sm text-critical">
              Could not load this patient. {state.error}
            </p>
          )}
          {detail && <Summary summary={detail.summary} />}
          {detail && <Resources resources={detail.resources} highlight={highlight} />}
        </div>
      </aside>
    </div>
  );
}

function Summary({ summary }) {
  if (!summary) {
    return (
      <section className="rounded border border-dashed border-line p-4 text-sm text-muted">
        No AI summary has been generated for this patient yet.
      </section>
    );
  }
  return (
    <section aria-labelledby="summary-title">
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <h3 id="summary-title" className="text-sm font-medium text-summary">
          AI summary
        </h3>
        <p className="text-xs text-muted">
          Confidence {summary.confidence} · {summary.word_count} words · {summary.model}
          {summary.generated_at && ` · ${formatDate(summary.generated_at.slice(0, 10))}`}
        </p>
      </div>

      <p className="mt-2 font-serif text-lg leading-snug text-ink">{summary.chief_concern}</p>

      {summary.key_diagnoses.length > 0 && (
        <Block title="Key diagnoses">
          <ul className="list-disc space-y-0.5 pl-5 font-serif text-[15px] text-ink">
            {summary.key_diagnoses.map((d) => (
              <li key={d}>{d}</li>
            ))}
          </ul>
        </Block>
      )}

      {summary.recent_media.length > 0 && (
        <Block title="Recent records">
          <ul className="space-y-2">
            {summary.recent_media.map((m) => (
              <li key={m.resource_id} className="grid grid-cols-[6.5rem_1fr] gap-x-3 text-sm">
                <span className="text-muted">
                  <span className="capitalize">{m.type}</span>
                  <br />
                  <time className="tabular-nums text-xs">{formatDate(m.date)}</time>
                </span>
                <span className="font-serif text-[15px] text-ink">{m.note}</span>
              </li>
            ))}
          </ul>
        </Block>
      )}

      {summary.anomalies.length > 0 && (
        <Block title="Flagged anomalies">
          <ul className="space-y-1">
            {summary.anomalies.map((a) => (
              <li key={a} className="flex gap-2 font-serif text-[15px] text-ink">
                <span aria-hidden="true" className="mt-2 size-1.5 shrink-0 rounded-full bg-warning" />
                {a}
              </li>
            ))}
          </ul>
        </Block>
      )}

      <p className="mt-5 border-l-2 border-warning bg-warning-tint px-3 py-2 text-xs leading-relaxed text-ink">
        {summary.disclaimer}
      </p>
    </section>
  );
}

function Block({ title, children }) {
  return (
    <div className="mt-4">
      <h4 className="text-xs font-medium text-muted">{title}</h4>
      <div className="mt-1.5">{children}</div>
    </div>
  );
}

function Resources({ resources, highlight }) {
  const cited = resources.filter((r) => r.cited).length;
  return (
    <section className="mt-8" aria-labelledby="resources-title">
      <div className="flex items-baseline justify-between">
        <h3 id="resources-title" className="text-sm font-medium text-ink">
          Linked FHIR resources
        </h3>
        <p className="text-xs text-muted">
          {resources.length} total · {cited} cited by the summary
        </p>
      </div>
      {resources.length === 0 ? (
        <p className="mt-2 text-sm text-muted">No resources in this patient's bundle.</p>
      ) : (
        <ul className="mt-2 divide-y divide-line-soft border-y border-line-soft">
          {resources.map((r) => {
            const isHighlight = r.resource_id === highlight;
            return (
              <li
                key={r.resource_id}
                data-resource-id={r.resource_id}
                className={`grid grid-cols-[1fr_auto] gap-x-3 py-2 text-sm ${isHighlight ? "bg-primary-tint -mx-2 px-2" : ""}`}
              >
                <div className="min-w-0">
                  <p className="truncate text-ink">{r.title}</p>
                  <p className="mt-0.5 flex flex-wrap items-center gap-x-3 text-xs text-muted">
                    <TypeTag resourceType={r.resource_type} />
                    <time className="tabular-nums">{formatDate(r.date)}</time>
                    {isHighlight && <span className="text-primary">Matched your search</span>}
                  </p>
                </div>
                <span className={`self-center text-xs ${r.cited ? "text-summary" : "text-faint"}`}>
                  {r.cited ? "Cited" : ""}
                </span>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
