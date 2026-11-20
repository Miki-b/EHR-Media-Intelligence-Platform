import { useEffect, useState } from "react";
import { getHealth } from "./api.js";

// Placeholder shell. Task 5 replaces this with SearchBar, Filters, ResultList
// and PatientDrawer.
export default function App() {
  const [status, setStatus] = useState("checking…");

  useEffect(() => {
    getHealth()
      .then((h) => setStatus(`API ${h.status} · LLM: ${h.llm_provider}`))
      .catch(() => setStatus("API not reachable. Run `make api`."));
  }, []);

  return (
    <main className="mx-auto max-w-5xl p-6">
      <h1 className="text-2xl font-semibold">EHR Media Intelligence</h1>
      <p className="mt-1 text-slate-600">Search across synthetic patient records.</p>
      <p role="status" aria-live="polite" className="mt-6 text-sm text-slate-500">
        {status}
      </p>
    </main>
  );
}
