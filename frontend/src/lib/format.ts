// Pure display helpers (unit tested with `npm test`). Erasable TypeScript only, so Node can run
// the tests directly without a build step.

/** Indian grouping, e.g. "1234567.5" -> "₹12,34,567.50". Null and junk render as an em dash. */
export function formatMoney(value: string | null | undefined): string {
  if (value === null || value === undefined || value === "") return "—";
  const n = Number(value);
  if (!Number.isFinite(n)) return "—";
  return "₹" + n.toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

export function formatQty(value: string | null | undefined): string {
  if (value === null || value === undefined || value === "") return "—";
  const n = Number(value);
  return Number.isFinite(n) ? String(n) : "—";
}

export type ConfidenceLevel = "high" | "medium" | "low";

/** 0.9 is the policy's default minimum, so anything below it is flagged. */
export function confidenceLevel(confidence: number, minimum = 0.9): ConfidenceLevel {
  if (confidence >= minimum) return "high";
  if (confidence >= 0.6) return "medium";
  return "low";
}

export function statusLabel(status: string): string {
  return status
    .toLowerCase()
    .split("_")
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(" ");
}

/** Human labels for the check names produced by the rules engine. */
export const CHECK_LABELS: Record<string, string> = {
  gstin_format: "GSTIN format",
  gstin_matches_vendor: "GSTIN matches vendor",
  tax_type: "Tax type (CGST/SGST vs IGST)",
  arithmetic: "Arithmetic",
  po_exists: "Purchase order exists",
  po_vendor_match: "PO belongs to vendor",
  unit_price: "Unit prices match PO",
  quantity: "Quantities received",
  line_coverage: "Every line is on the PO",
  exact_duplicate: "Exact duplicate",
  possible_duplicate: "Possible duplicate",
};

export function checkLabel(name: string): string {
  return CHECK_LABELS[name] ?? name;
}
