// Single place for API calls. Shapes match app/contracts.py.
const BASE = import.meta.env.VITE_API_BASE ?? "/api";

async function request(path, options = {}) {
  const res = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.json();
}

export const getHealth = () => request("/health");

// filters: { resource_type?, date_from?, date_to? } -> query params, as the spec asks
export function search(query, filters = {}, { signal, topK = 5 } = {}) {
  const params = new URLSearchParams(
    Object.entries(filters).filter(([, v]) => v),
  ).toString();
  return request(`/search${params ? `?${params}` : ""}`, {
    method: "POST",
    body: JSON.stringify({ query, top_k: topK }),
    signal,
  });
}

export const getPatient = (patientId) => request(`/patients/${encodeURIComponent(patientId)}`);
