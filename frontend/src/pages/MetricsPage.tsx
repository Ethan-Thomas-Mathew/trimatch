import { useEffect, useState } from "react";

import { api } from "../api.ts";
import { formatCostInr, statusLabel } from "../lib/format.ts";
import type { Metrics } from "../types.ts";

function Stat({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="card p-4">
      <div className="text-xs uppercase tracking-wide text-slate-500">{label}</div>
      <div className="mt-1 text-2xl font-semibold tabular-nums">{value}</div>
      {hint && <div className="mt-1 text-xs text-slate-500">{hint}</div>}
    </div>
  );
}

function Bars({ title, data }: { title: string; data: Record<string, number> }) {
  const entries = Object.entries(data).sort((a, b) => b[1] - a[1]);
  const max = Math.max(1, ...entries.map(([, n]) => n));
  return (
    <section className="card p-4" aria-label={title}>
      <h2 className="mb-3 text-sm font-semibold">{title}</h2>
      {entries.length === 0 && <p className="text-sm text-slate-500">No data yet.</p>}
      <ul className="space-y-2">
        {entries.map(([k, n]) => (
          <li key={k} className="text-sm">
            <div className="flex justify-between">
              <span>{statusLabel(k)}</span>
              <span className="tabular-nums">{n}</span>
            </div>
            <div className="mt-1 h-2 rounded bg-slate-100" aria-hidden="true">
              <div className="h-2 rounded bg-slate-700" style={{ width: `${(n / max) * 100}%` }} />
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}

export function MetricsPage() {
  const [m, setM] = useState<Metrics | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .metrics()
      .then(setM)
      .catch((e: unknown) => setError(e instanceof Error ? e.message : "Could not load metrics."));
  }, []);

  if (error)
    return (
      <p role="alert" className="text-rose-700">
        {error}
      </p>
    );
  if (!m) return <p className="text-slate-500">Loading…</p>;

  return (
    <div className="space-y-4">
      <h1 className="text-xl font-semibold">Metrics</h1>
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Stat label="Invoices" value={String(m.invoices)} />
        <Stat label="Awaiting review" value={String(m.awaiting_review)} />
        <Stat
          label="Average processing time"
          value={`${m.avg_pipeline_seconds.toFixed(1)} s`}
          hint={`extraction alone ${(m.avg_extraction_latency_ms / 1000).toFixed(1)} s`}
        />
        <Stat
          label="Average cost per invoice"
          value={formatCostInr(m.avg_cost_inr, m.avg_cost_usd)}
          hint="local models cost nothing per call"
        />
      </div>
      <div className="grid gap-4 md:grid-cols-2">
        <Bars title="By status" data={m.by_status} />
        <Bars title="By latest decision" data={m.by_decision} />
      </div>
    </div>
  );
}
