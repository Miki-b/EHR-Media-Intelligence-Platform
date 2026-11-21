import { RESOURCE_TYPES } from "../lib/format.js";

const dateField =
  "rounded-lg border border-line bg-panel px-2.5 py-1.5 text-sm text-ink focus:border-primary focus:outline-none focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/40";

// Record type as a segmented control of real radio buttons: one click to switch, arrow
// keys move between options, and screen readers announce it as a radio group.
function TypeSegments({ value, onChange }) {
  return (
    <fieldset className="flex flex-col gap-1">
      <legend className="mb-1 text-xs text-muted">Record type</legend>
      <div className="flex flex-wrap gap-1 rounded-xl border border-line bg-panel p-1">
        {RESOURCE_TYPES.map((t) => (
          <label key={t.value} className="relative">
            <input
              type="radio"
              name="resource_type"
              value={t.value}
              checked={value === t.value}
              onChange={() => onChange(t.value)}
              className="peer sr-only"
            />
            <span className="block cursor-pointer rounded-lg px-3 py-1.5 text-sm text-muted transition hover:text-primary peer-checked:bg-primary peer-checked:font-medium peer-checked:text-white peer-checked:hover:bg-primary-hover peer-checked:hover:text-white peer-focus-visible:outline-2 peer-focus-visible:outline-offset-2 peer-focus-visible:outline-primary">
              {t.label}
            </span>
          </label>
        ))}
      </div>
    </fieldset>
  );
}

export default function Filters({ filters, onChange }) {
  const set = (key) => (e) => onChange({ ...filters, [key]: e.target.value });
  const active = Object.values(filters).some(Boolean);
  const invalidRange = filters.date_from && filters.date_to && filters.date_from > filters.date_to;

  return (
    <fieldset className="mt-5 flex flex-wrap items-end gap-x-5 gap-y-3">
      <legend className="sr-only">Filter results</legend>

      <TypeSegments
        value={filters.resource_type}
        onChange={(v) => onChange({ ...filters, resource_type: v })}
      />

      <label className="flex flex-col gap-1 text-xs text-muted">
        From
        <input
          type="date"
          value={filters.date_from}
          max={filters.date_to || undefined}
          onChange={set("date_from")}
          aria-invalid={invalidRange || undefined}
          className={dateField}
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
          className={dateField}
        />
      </label>

      {active && (
        <button
          type="button"
          onClick={() => onChange({ resource_type: "", date_from: "", date_to: "" })}
          className="pb-1.5 text-sm text-primary underline-offset-2 hover:text-primary-hover hover:underline"
        >
          Clear filters
        </button>
      )}
    </fieldset>
  );
}
