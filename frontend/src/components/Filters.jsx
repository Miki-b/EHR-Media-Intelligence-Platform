import { RESOURCE_TYPES } from "../lib/format.js";

const field =
  "rounded border border-line bg-panel px-2 py-1.5 text-sm text-ink focus:border-signal focus:outline-none focus-visible:outline-none";

export default function Filters({ filters, onChange }) {
  const set = (key) => (e) => onChange({ ...filters, [key]: e.target.value });
  const active = Object.values(filters).some(Boolean);
  const invalidRange = filters.date_from && filters.date_to && filters.date_from > filters.date_to;

  return (
    <fieldset className="mt-4 flex flex-wrap items-end gap-x-5 gap-y-3">
      <legend className="sr-only">Filter results</legend>

      <label className="flex flex-col gap-1 text-xs text-muted">
        Record type
        <select value={filters.resource_type} onChange={set("resource_type")} className={field}>
          {RESOURCE_TYPES.map((t) => (
            <option key={t.value} value={t.value}>
              {t.label}
            </option>
          ))}
        </select>
      </label>

      <label className="flex flex-col gap-1 text-xs text-muted">
        From
        <input
          type="date"
          value={filters.date_from}
          max={filters.date_to || undefined}
          onChange={set("date_from")}
          aria-invalid={invalidRange || undefined}
          className={field}
        />
      </label>

      <label className="flex flex-col gap-1 text-xs text-muted">
        To
        <input
          type="date"
          value={filters.date_to}
          min={filters.date_from || undefined}
          onChange={set("date_to")}
          aria-invalid={invalidRange || undefined}
          className={field}
        />
      </label>

      {active && (
        <button
          type="button"
          onClick={() => onChange({ resource_type: "", date_from: "", date_to: "" })}
          className="pb-1.5 text-sm text-signal underline-offset-2 hover:underline"
        >
          Clear filters
        </button>
      )}
    </fieldset>
  );
}
