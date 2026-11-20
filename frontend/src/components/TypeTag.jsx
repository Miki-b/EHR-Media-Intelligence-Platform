import { typeMeta } from "../lib/format.js";

// A small colour swatch plus a plain word. The swatch doubles as the legend
// in the drawer's resource list, so the same colours mean the same thing everywhere.
export default function TypeTag({ resourceType, className = "" }) {
  const meta = typeMeta(resourceType);
  return (
    <span className={`inline-flex items-center gap-1.5 text-xs font-medium ${meta.text} ${className}`}>
      <span aria-hidden="true" className={`inline-block size-2 rounded-[2px] ${meta.swatch}`} />
      {meta.label}
    </span>
  );
}
