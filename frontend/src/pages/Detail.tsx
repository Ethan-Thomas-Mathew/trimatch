import { FormEvent, useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { api } from "../api.ts";
import { StatusBadge } from "../components/StatusBadge.tsx";
import { checkLabel, confidenceLevel, formatMoney, formatQty } from "../lib/format.ts";
import type { Check, InvoiceDetail, ScoredField } from "../types.ts";

const FIELD_LABELS: [string, string][] = [
  ["vendor_name", "Vendor"],
  ["vendor_gstin", "Vendor GSTIN"],
  ["invoice_number", "Invoice number"],
  ["invoice_date", "Invoice date"],
  ["po_number", "PO number"],
  ["subtotal", "Subtotal"],
  ["cgst", "CGST"],
  ["sgst", "SGST"],
  ["igst", "IGST"],
  ["tax_total", "Tax total"],
  ["grand_total", "Grand total"],
];
const MONEY_FIELDS = new Set(["subtotal", "cgst", "sgst", "igst", "tax_total", "grand_total"]);

function isScored(v: unknown): v is ScoredField {
  return typeof v === "object" && v !== null && "confidence" in v;
}

const CONFIDENCE_STYLE = {
  high: "bg-emerald-100 text-emerald-800",
  medium: "bg-amber-100 text-amber-800",
  low: "bg-rose-100 text-rose-800",
} as const;

export function Detail() {
  const { id } = useParams();
  const invoiceId = Number(id);
  const [inv, setInv] = useState<InvoiceDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pdf, setPdf] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setInv(await api.get(invoiceId));
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not load the invoice.");
    }
  }, [invoiceId]);

  useEffect(() => {
    void load();
  }, [load]);

  // Poll while the background pipeline is still working.
  const processing = inv?.status === "PROCESSING";
  useEffect(() => {
    if (!processing) return;
    const timer = setInterval(() => void load(), 1500);
    return () => clearInterval(timer);
  }, [processing, load]);

  // Fetch the PDF once we know there is one; release the blob URL when leaving.
  const hasFile = inv?.has_file ?? false;
  useEffect(() => {
    if (!hasFile) return;
    let url: string | null = null;
    let cancelled = false;
    api
      .pdfUrl(invoiceId)
      .then((u) => {
        if (cancelled) URL.revokeObjectURL(u);
        else {
          url = u;
          setPdf(u);
        }
      })
      .catch(() => setPdf(null));
    return () => {
      cancelled = true;
      if (url) URL.revokeObjectURL(url);
    };
  }, [hasFile, invoiceId]);

  if (error && !inv) {
    return (
      <div className="space-y-3">
        <p role="alert" className="text-rose-700">
          {error}
        </p>
        <Link to="/" className="text-sky-700 hover:underline">
          ← Back to inbox
        </Link>
      </div>
    );
  }
  if (!inv) return <p className="text-slate-500">Loading…</p>;

  const failed = inv.checks.filter((c) => !c.passed);
  const passed = inv.checks.filter((c) => c.passed);
  const reviewable = inv.status === "NEEDS_REVIEW" || inv.status === "FAILED";

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <Link to="/" className="text-sm text-sky-700 hover:underline">
          ← Inbox
        </Link>
        <h1 className="text-xl font-semibold">Invoice #{inv.id}</h1>
        <StatusBadge status={inv.status} />
        {processing && <span className="text-sm text-slate-500">Reading and checking the invoice…</span>}
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        {/* Left: the original PDF */}
        <section aria-label="Invoice PDF" className="card h-[80vh] overflow-hidden lg:sticky lg:top-4">
          {pdf ? (
            <iframe title="Invoice PDF" src={pdf} className="h-full w-full" />
          ) : (
            <div className="flex h-full items-center justify-center text-sm text-slate-500">
              {inv.has_file ? "Loading PDF…" : "No PDF stored for this invoice."}
            </div>
          )}
        </section>

        {/* Right: what the system found */}
        <div className="space-y-4">
          {inv.decision && (
            <section
              aria-label="Decision"
              className={`card p-4 ${
                inv.decision.decision === "AUTO_APPROVE" || inv.decision.decision === "APPROVE"
                  ? "border-emerald-300"
                  : inv.decision.decision === "REJECT"
                    ? "border-rose-300"
                    : "border-amber-300"
              }`}
            >
              <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-500">
                Decision: {inv.decision.decision.replace("_", " ")}{" "}
                <span className="font-normal normal-case">
                  ({inv.decision.decided_by === "policy" ? "by the rules engine" : "by a human reviewer"})
                </span>
              </h2>
              {inv.decision.reasons.length > 0 && (
                <ul className="mt-2 list-disc space-y-1 pl-5 text-sm">
                  {inv.decision.reasons.map((r, i) => (
                    <li key={i}>{r.message}</li>
                  ))}
                </ul>
              )}
              {inv.decision.explanation && (
                <div className="mt-3 rounded-md bg-slate-50 p-3 text-sm">
                  <div className="mb-1 text-xs font-medium uppercase tracking-wide text-slate-500">
                    Plain-English summary (display only; it cannot change the decision)
                  </div>
                  <p className="whitespace-pre-wrap">{inv.decision.explanation}</p>
                </div>
              )}
            </section>
          )}

          {failed.length > 0 && (
            <section aria-label="Failed checks" className="card border-rose-300 p-4">
              <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-rose-700">
                Failed checks ({failed.length})
              </h2>
              <ul className="space-y-3">
                {failed.map((c) => (
                  <FailedCheck key={c.check} c={c} />
                ))}
              </ul>
            </section>
          )}

          {passed.length > 0 && (
            <details className="card p-4">
              <summary className="cursor-pointer text-sm font-semibold">
                Passed checks ({passed.length})
              </summary>
              <ul className="mt-2 space-y-1 text-sm text-slate-700">
                {passed.map((c) => (
                  <li key={c.check}>
                    <span aria-hidden="true" className="text-emerald-600">
                      ✓{" "}
                    </span>
                    {checkLabel(c.check)}
                  </li>
                ))}
              </ul>
            </details>
          )}

          <ExtractedFields inv={inv} />

          {inv.lines.length > 0 && (
            <section aria-label="Line items" className="card overflow-x-auto">
              <table className="w-full text-left text-sm">
                <caption className="p-4 pb-2 text-left text-sm font-semibold">Line items</caption>
                <thead className="border-b border-slate-200 text-xs uppercase text-slate-500">
                  <tr>
                    <th className="px-4 py-2">Item</th>
                    <th className="px-4 py-2 text-right">Qty</th>
                    <th className="px-4 py-2 text-right">Price</th>
                    <th className="px-4 py-2 text-right">Total</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  {inv.lines.map((l, i) => (
                    <tr key={i}>
                      <td className="px-4 py-2">
                        {l.description}
                        {l.sku && <span className="ml-2 text-xs text-slate-400">{l.sku}</span>}
                      </td>
                      <td className="px-4 py-2 text-right tabular-nums">{formatQty(l.qty)}</td>
                      <td className="px-4 py-2 text-right tabular-nums">{formatMoney(l.unit_price)}</td>
                      <td className="px-4 py-2 text-right tabular-nums">{formatMoney(l.line_total)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </section>
          )}

          {reviewable && <ReviewForm id={inv.id} onDone={setInv} />}

          {inv.reviews.length > 0 && (
            <section aria-label="Review history" className="card p-4">
              <h2 className="mb-2 text-sm font-semibold">Review history</h2>
              <ul className="space-y-2 text-sm">
                {inv.reviews.map((r, i) => (
                  <li key={i}>
                    <strong>{r.reviewer}</strong> {r.action === "approve" ? "approved" : "rejected"} on{" "}
                    {new Date(r.created_at).toLocaleString()}: “{r.comment}”
                  </li>
                ))}
              </ul>
            </section>
          )}

          <Trace inv={inv} />
        </div>
      </div>
    </div>
  );
}

function FailedCheck({ c }: { c: Check }) {
  return (
    <li className="text-sm">
      <div className="font-medium text-rose-800">{checkLabel(c.check)}</div>
      <p className="text-slate-700">{c.message}</p>
      <dl className="mt-1 grid grid-cols-[auto_1fr] gap-x-3 text-xs">
        <dt className="text-slate-500">Expected</dt>
        <dd className="break-words">{c.expected ?? "—"}</dd>
        <dt className="text-slate-500">Actual</dt>
        <dd className="break-words font-medium text-rose-700">{c.actual ?? "—"}</dd>
      </dl>
    </li>
  );
}

function ExtractedFields({ inv }: { inv: InvoiceDetail }) {
  const fields = inv.extraction?.fields;
  if (!fields) return null;
  return (
    <section aria-label="Extracted fields" className="card overflow-x-auto">
      <table className="w-full text-left text-sm">
        <caption className="p-4 pb-2 text-left text-sm font-semibold">
          Extracted fields{" "}
          {inv.extraction && (
            <span className="font-normal text-slate-500">
              (model {inv.extraction.model}; lowest confidence{" "}
              {inv.extraction.min_confidence === null ? "—" : inv.extraction.min_confidence.toFixed(2)})
            </span>
          )}
        </caption>
        <thead className="sr-only">
          <tr>
            <th>Field</th>
            <th>Value</th>
            <th>Confidence</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-100">
          {FIELD_LABELS.map(([key, label]) => {
            const f = fields[key];
            if (!isScored(f)) return null;
            const shown =
              f.value === null || f.value === undefined
                ? "—"
                : MONEY_FIELDS.has(key)
                  ? formatMoney(String(f.value))
                  : String(f.value);
            const level = confidenceLevel(f.confidence);
            return (
              <tr key={key}>
                <th scope="row" className="px-4 py-2 font-normal text-slate-500">
                  {label}
                </th>
                <td className="break-words px-4 py-2">{shown}</td>
                <td className="px-4 py-2 text-right">
                  <span
                    className={`rounded px-1.5 py-0.5 text-xs tabular-nums ${CONFIDENCE_STYLE[level]}`}
                    title={level === "high" ? "Confident" : "Below the 0.90 confidence minimum"}
                  >
                    {f.confidence.toFixed(2)}
                  </span>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </section>
  );
}

function ReviewForm({ id, onDone }: { id: number; onDone: (inv: InvoiceDetail) => void }) {
  const [comment, setComment] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function act(action: "approve" | "reject") {
    if (!comment.trim()) {
      setError("A comment is required.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      onDone(await api.review(id, action, comment.trim()));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not save your review.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <form
      aria-label="Review"
      className="card space-y-3 border-slate-400 p-4"
      onSubmit={(e: FormEvent) => e.preventDefault()}
    >
      <h2 className="text-sm font-semibold">Your review</h2>
      <div>
        <label htmlFor="comment" className="block text-sm">
          Comment <span className="text-rose-600">(required)</span>
        </label>
        <textarea
          id="comment"
          className="mt-1 w-full rounded-md border border-slate-300 p-2 text-sm"
          rows={3}
          maxLength={2000}
          value={comment}
          onChange={(e) => setComment(e.target.value)}
          aria-describedby="comment-error"
        />
      </div>
      <div id="comment-error" role="alert" aria-live="polite" className="min-h-5 text-sm text-rose-700">
        {error}
      </div>
      <div className="flex gap-2">
        <button type="button" className="btn-approve" disabled={busy} onClick={() => void act("approve")}>
          Approve
        </button>
        <button type="button" className="btn-reject" disabled={busy} onClick={() => void act("reject")}>
          Reject
        </button>
      </div>
    </form>
  );
}

function Trace({ inv }: { inv: InvoiceDetail }) {
  if (inv.trace.length === 0) return null;
  return (
    <details className="card p-4">
      <summary className="cursor-pointer text-sm font-semibold">
        Audit trail and agent trace ({inv.trace.length} events)
      </summary>
      <ol className="mt-3 space-y-2 text-xs">
        {inv.trace.map((e, i) => {
          const p = e.payload as Record<string, unknown>;
          const detail = [
            p.tool ? `tool ${String(p.tool)}` : null,
            p.error ? `error: ${String(p.error)}` : null,
            p.tokens_in !== undefined ? `${String(p.tokens_in)} in / ${String(p.tokens_out)} out tokens` : null,
            p.latency_ms !== undefined ? `${String(p.latency_ms)} ms` : null,
            p.decision ? `decision ${String(p.decision)}` : null,
          ]
            .filter(Boolean)
            .join(" · ");
          return (
            <li key={i} className="rounded bg-slate-50 p-2">
              <span className="font-mono font-semibold">{e.action}</span>{" "}
              <span className="text-slate-500">by {e.actor}</span>
              {detail && <div className="text-slate-700">{detail}</div>}
              {typeof p.result_preview === "string" && (
                <div className="break-all font-mono text-slate-500">{p.result_preview}</div>
              )}
            </li>
          );
        })}
      </ol>
    </details>
  );
}
