// Single place for API calls. Shapes match app/contracts.py.
const BASE = import.meta.env.VITE_API_BASE ?? "/api";

export class ApiError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

async function request(path, options = {}) {
  let res;
  try {
    res = await fetch(`${BASE}${path}`, {
      headers: { "Content-Type": "application/json" },
      ...options,
    });
  } catch (err) {
    if (err.name === "AbortError") throw err;
    throw new ApiError(0, "The API is not reachable.");
  }
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      if (typeof body.detail === "string") detail = body.detail;
    } catch {
      /* keep statusText */
    }
    throw new ApiError(res.status, detail);
  }
  return res.json();
}

export const getHealth = (options) => request("/health", options);

// filters: { resource_type?, date_from?, date_to? } -> query params, as the spec asks
export function search(query, filters = {}, { signal, topK = 5 } = {}) {
  const params = new URLSearchParams(Object.entries(filters).filter(([, v]) => v)).toString();
  return request(`/search${params ? `?${params}` : ""}`, {
    method: "POST",
    body: JSON.stringify({ query, top_k: topK }),
    signal,
  });
}

export const getPatient = (patientId, options) =>
  request(`/patients/${encodeURIComponent(patientId)}`, options);
