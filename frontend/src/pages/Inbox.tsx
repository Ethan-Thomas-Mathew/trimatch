import { ChangeEvent, DragEvent, useCallback, useEffect, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { api } from "../api.ts";
import { StatusBadge } from "../components/StatusBadge.tsx";
import { formatMoney, statusLabel } from "../lib/format.ts";
import { ALL_STATUSES, type InvoiceList } from "../types.ts";

const PAGE = 25;

export function Inbox() {
  const navigate = useNavigate();
  const [status, setStatus] = useState<string>("NEEDS_REVIEW");
  const [offset, setOffset] = useState(0);
  const [data, setData] = useState<InvoiceList | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const [dragging, setDragging] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);

  const load = useCallback(async () => {
    try {
      setData(await api.list({ status: status || undefined, limit: PAGE, offset }));
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not load invoices.");
    }
  }, [status, offset]);

  useEffect(() => {
    void load();
  }, [load]);

  // Keep the list fresh while anything is still being processed.
  const anyProcessing = data?.items.some((i) => i.status === "PROCESSING") ?? false;
  useEffect(() => {
    if (!anyProcessing) return;
    const timer = setInterval(() => void load(), 2000);
    return () => clearInterval(timer);
  }, [anyProcessing, load]);

  async function upload(file: File | undefined) {
    if (!file) return;
    if (file.type !== "application/pdf" && !file.name.toLowerCase().endsWith(".pdf")) {
      setError("Only PDF files can be uploaded.");
      return;
    }
    setUploading(true);
    setError(null);
    try {
      const created = await api.upload(file);
      navigate(`/invoices/${created.id}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Upload failed.");
    } finally {
      setUploading(false);
    }
  }

  const onPick = (e: ChangeEvent<HTMLInputElement>) => {
    void upload(e.target.files?.[0]);
    e.target.value = "";
  };
  const onDrop = (e: DragEvent) => {
    e.preventDefault();
    setDragging(false);
    void upload(e.dataTransfer.files[0]);
  };

  const total = data?.total ?? 0;

  return (
    <div className="space-y-4">
      <div
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
        className={`card flex flex-wrap items-center gap-3 border-dashed p-4 ${
          dragging ? "border-slate-900 bg-slate-100" : ""
        }`}
      >
        <div className="flex-1 text-sm text-slate-600">
          Drop a supplier invoice PDF here, or choose a file. It is read, matched against the PO and
          goods receipts, and decided by the rules engine.
        </div>
        <input
          ref={fileInput}
          type="file"
          accept="application/pdf,.pdf"
          className="sr-only"
          aria-label="Choose an invoice PDF"
          onChange={onPick}
        />
        <button className="btn-primary" disabled={uploading} onClick={() => fileInput.current?.click()}>
          {uploading ? "Uploading…" : "Upload invoice"}
        </button>
      </div>

      <div role="tablist" aria-label="Filter by status" className="flex flex-wrap gap-1">
        {["", ...ALL_STATUSES].map((s) => (
          <button
            key={s || "all"}
            role="tab"
            aria-selected={status === s}
            onClick={() => {
              setStatus(s);
              setOffset(0);
            }}
            className={`rounded-full px-3 py-1 text-sm ${
              status === s ? "bg-slate-900 text-white" : "bg-white text-slate-700 ring-1 ring-slate-300 hover:bg-slate-100"
            }`}
          >
            {s ? statusLabel(s) : "All"}
          </button>
        ))}
      </div>

      <div role="alert" aria-live="polite" className="text-sm text-rose-700">
        {error}
      </div>

      <div className="card overflow-x-auto">
        <table className="w-full text-left text-sm">
          <thead className="border-b border-slate-200 bg-slate-50 text-xs uppercase tracking-wide text-slate-500">
            <tr>
              <th className="px-4 py-2">#</th>
              <th className="px-4 py-2">Vendor</th>
              <th className="px-4 py-2">Invoice no.</th>
              <th className="px-4 py-2">Date</th>
              <th className="px-4 py-2 text-right">Total</th>
              <th className="px-4 py-2">Status</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {data === null && (
              <tr>
                <td colSpan={6} className="px-4 py-6 text-center text-slate-500">
                  Loading…
                </td>
              </tr>
            )}
            {data?.items.length === 0 && (
              <tr>
                <td colSpan={6} className="px-4 py-6 text-center text-slate-500">
                  No invoices here.
                </td>
              </tr>
            )}
            {data?.items.map((i) => (
              <tr key={i.id} className="hover:bg-slate-50">
                <td className="px-4 py-2">
                  <Link className="font-medium text-sky-700 hover:underline" to={`/invoices/${i.id}`}>
                    {i.id}
                  </Link>
                </td>
                <td className="px-4 py-2">{i.vendor_name ?? <span className="text-slate-400">Unknown</span>}</td>
                <td className="px-4 py-2">{i.invoice_number ?? "—"}</td>
                <td className="px-4 py-2">{i.invoice_date ?? "—"}</td>
                <td className="px-4 py-2 text-right tabular-nums">{formatMoney(i.grand_total)}</td>
                <td className="px-4 py-2">
                  <StatusBadge status={i.status} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="flex items-center justify-between text-sm text-slate-600">
        <span>
          {total === 0 ? "0" : `${offset + 1}–${Math.min(offset + PAGE, total)}`} of {total}
        </span>
        <div className="flex gap-2">
          <button className="btn-ghost" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))}>
            Previous
          </button>
          <button className="btn-ghost" disabled={offset + PAGE >= total} onClick={() => setOffset(offset + PAGE)}>
            Next
          </button>
        </div>
      </div>
    </div>
  );
}
