import { clearCredentials, getAuthHeader } from "./lib/auth.ts";
import { describeError } from "./lib/errors.ts";
import type { InvoiceDetail, InvoiceList, Metrics } from "./types.ts";

const BASE = "/api"; // proxied to the FastAPI backend (Vite in dev, nginx in Docker)

export class ApiError extends Error {
  readonly status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function send(path: string, init: RequestInit = {}): Promise<Response> {
  const headers = new Headers(init.headers);
  const auth = getAuthHeader();
  if (auth) headers.set("Authorization", auth);
  // Tells the API this is our UI, so a wrong password gets a plain 401 and the browser does not
  // open its own native "Sign in to access this site" box over the app's sign-in form.
  headers.set("X-Requested-With", "TriMatch-UI");
  let response: Response;
  try {
    response = await fetch(BASE + path, { ...init, headers });
  } catch {
    throw new ApiError(0, "Cannot reach the server. Is the backend running?");
  }
  if (response.status === 401) {
    clearCredentials();
    throw new ApiError(401, "Invalid credentials. Please sign in again.");
  }
  if (!response.ok) {
    let body: unknown = null;
    try {
      body = await response.json();
    } catch {
      // not JSON; describeError falls back to the status code
    }
    throw new ApiError(response.status, describeError(body, response.status));
  }
  return response;
}

async function json<T>(path: string, init?: RequestInit): Promise<T> {
  return (await send(path, init)).json() as Promise<T>;
}

export const api = {
  metrics: () => json<Metrics>("/metrics"),

  list: (params: { status?: string; limit: number; offset: number }) => {
    const q = new URLSearchParams({ limit: String(params.limit), offset: String(params.offset) });
    if (params.status) q.set("status", params.status);
    return json<InvoiceList>(`/invoices?${q}`);
  },

  get: (id: number) => json<InvoiceDetail>(`/invoices/${id}`),

  upload: (file: File) => {
    const form = new FormData();
    form.append("file", file);
    return json<{ id: number; status: string }>("/invoices", { method: "POST", body: form });
  },

  review: (
    id: number,
    action: "approve" | "reject",
    comment: string,
    acknowledgeOverride = false,
  ) =>
    json<InvoiceDetail>(`/invoices/${id}/review`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action, comment, acknowledge_override: acknowledgeOverride }),
    }),

  /** The PDF needs the Authorization header, so it is fetched and shown from a blob URL. */
  pdfUrl: async (id: number): Promise<string> => {
    const blob = await (await send(`/invoices/${id}/file`)).blob();
    return URL.createObjectURL(new Blob([blob], { type: "application/pdf" }));
  },
};
