import { statusLabel } from "../lib/format.ts";

const STYLES: Record<string, string> = {
  NEEDS_REVIEW: "bg-amber-100 text-amber-900 ring-amber-300",
  AUTO_APPROVED: "bg-emerald-100 text-emerald-900 ring-emerald-300",
  APPROVED: "bg-emerald-100 text-emerald-900 ring-emerald-300",
  REJECTED: "bg-rose-100 text-rose-900 ring-rose-300",
  PROCESSING: "bg-sky-100 text-sky-900 ring-sky-300",
  FAILED: "bg-rose-100 text-rose-900 ring-rose-300",
};

export function StatusBadge({ status }: { status: string }) {
  return (
    <span
      className={`inline-block whitespace-nowrap rounded-full px-2.5 py-0.5 text-xs font-medium ring-1 ring-inset ${
        STYLES[status] ?? "bg-slate-100 text-slate-800 ring-slate-300"
      }`}
    >
      {statusLabel(status)}
    </span>
  );
}
