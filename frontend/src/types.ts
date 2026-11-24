// Mirrors the API's response models (backend/app/api/schemas.py). Money is an exact decimal
// string; convert only for display.

export type InvoiceStatus =
  | "PROCESSING"
  | "AUTO_APPROVED"
  | "NEEDS_REVIEW"
  | "APPROVED"
  | "REJECTED"
  | "FAILED";

export const ALL_STATUSES: InvoiceStatus[] = [
  "NEEDS_REVIEW",
  "AUTO_APPROVED",
  "APPROVED",
  "REJECTED",
  "PROCESSING",
  "FAILED",
];

export interface InvoiceSummary {
  id: number;
  status: InvoiceStatus;
  vendor_name: string | null;
  invoice_number: string | null;
  invoice_date: string | null;
  grand_total: string | null;
  decision: string | null;
  decided_by: string | null;
}

export interface InvoiceList {
  items: InvoiceSummary[];
  total: number;
}

export interface Line {
  sku: string | null;
  description: string;
  qty: string;
  unit_price: string;
  line_total: string;
}

export interface Check {
  check: string;
  passed: boolean;
  expected: string | null;
  actual: string | null;
  message: string;
}

export interface Decision {
  decision: string;
  decided_by: string;
  reasons: { code: string; message: string }[];
  explanation: string | null;
}

export interface ReviewAction {
  reviewer: string;
  action: string;
  comment: string;
  created_at: string;
}

export interface TraceEvent {
  action: string;
  actor: string;
  created_at: string;
  payload: Record<string, unknown>;
}

export interface ScoredField {
  value: string | number | null;
  confidence: number;
}

export interface Extraction {
  model: string;
  fields: Record<string, ScoredField | unknown>;
  min_confidence: number | null;
  tokens_in: number;
  tokens_out: number;
  latency_ms: number;
}

export interface InvoiceDetail {
  id: number;
  status: InvoiceStatus;
  has_file: boolean;
  vendor_name: string | null;
  invoice_number: string | null;
  invoice_date: string | null;
  po_number: string | null;
  subtotal: string | null;
  tax_total: string | null;
  grand_total: string | null;
  lines: Line[];
  extraction: Extraction | null;
  checks: Check[];
  decision: Decision | null;
  reviews: ReviewAction[];
  trace: TraceEvent[];
}

export interface Metrics {
  invoices: number;
  by_status: Record<string, number>;
  by_decision: Record<string, number>;
  awaiting_review: number;
  avg_cost_usd: number;
  avg_extraction_latency_ms: number;
  avg_pipeline_seconds: number;
}
