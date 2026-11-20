// Display helpers shared by the result list and the patient drawer.

export const RESOURCE_TYPES = [
  { value: "", label: "All record types" },
  { value: "DocumentReference", label: "Documents and notes" },
  { value: "DiagnosticReport", label: "Lab and imaging reports" },
  { value: "Summary", label: "AI summaries" },
];

// Short label and colour token per FHIR resource type. Anything unknown is grey.
const TYPE_META = {
  DocumentReference: { label: "Document", swatch: "bg-doc", text: "text-doc" },
  DiagnosticReport: { label: "Diagnostic report", swatch: "bg-report", text: "text-report" },
  Summary: { label: "AI summary", swatch: "bg-summary", text: "text-summary" },
  Observation: { label: "Observation", swatch: "bg-faint", text: "text-muted" },
  Encounter: { label: "Encounter", swatch: "bg-faint", text: "text-muted" },
};

export function typeMeta(resourceType) {
  return TYPE_META[resourceType] ?? { label: resourceType, swatch: "bg-faint", text: "text-muted" };
}

const DATE_FMT = new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short", year: "numeric" });

export function formatDate(iso) {
  if (!iso) return "No date";
  const d = new Date(`${iso}T00:00:00`);
  return Number.isNaN(d.getTime()) ? iso : DATE_FMT.format(d);
}

export function formatScore(score) {
  return (Math.round(score * 100) / 100).toFixed(2);
}

export function ageFrom(dobIso) {
  if (!dobIso) return null;
  const dob = new Date(`${dobIso}T00:00:00`);
  if (Number.isNaN(dob.getTime())) return null;
  const now = new Date();
  let age = now.getFullYear() - dob.getFullYear();
  const beforeBirthday =
    now.getMonth() < dob.getMonth() ||
    (now.getMonth() === dob.getMonth() && now.getDate() < dob.getDate());
  if (beforeBirthday) age -= 1;
  return age;
}

export function genderLabel(gender) {
  return { male: "Male", female: "Female", other: "Other", unknown: "Sex not recorded" }[gender] ?? gender;
}

// Split "DiagnosticReport/uuid" so the UI can show the type without the id noise.
export function splitResourceId(resourceId) {
  const i = resourceId.indexOf("/");
  return i === -1
    ? { type: resourceId, id: "" }
    : { type: resourceId.slice(0, i), id: resourceId.slice(i + 1) };
}
