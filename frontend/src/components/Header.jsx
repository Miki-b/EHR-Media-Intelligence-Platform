import { useEffect, useState } from "react";
import { getHealth } from "../api.js";

export default function Header() {
  const [health, setHealth] = useState(null);
  const [down, setDown] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    getHealth({ signal: controller.signal })
      .then((h) => setHealth(h))
      .catch((err) => err.name !== "AbortError" && setDown(true));
    return () => controller.abort();
  }, []);

  let status = "Checking the API…";
  if (down) status = "API not reachable";
  else if (health) {
    status = `${health.indexed_chunks} indexed records from ${health.patients} patients`;
  }

  return (
    <header className="border-t-[3px] border-b border-line border-t-primary bg-panel">
      <div className="mx-auto flex max-w-6xl flex-wrap items-baseline justify-between gap-x-6 gap-y-1 px-4 py-3 sm:px-6">
        <h1 className="text-base font-semibold tracking-tight text-primary">EHR Media Intelligence</h1>
        <p className={`text-sm ${down ? "text-critical" : "text-muted"}`} role="status">
          {status}
        </p>
      </div>
    </header>
  );
}
