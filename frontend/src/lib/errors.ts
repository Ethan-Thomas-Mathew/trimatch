// Turn an API error body into a sentence a person can read. FastAPI sends either
// {"detail": "text"} or {"detail": [{"loc": [...], "msg": "..."}]} for validation errors.

export function describeError(body: unknown, status: number): string {
  if (typeof body === "object" && body !== null && "detail" in body) {
    const detail = (body as { detail: unknown }).detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) {
      const parts = detail
        .map((d) => {
          if (typeof d !== "object" || d === null) return null;
          const { loc, msg } = d as { loc?: unknown[]; msg?: string };
          const field = Array.isArray(loc) ? String(loc[loc.length - 1] ?? "") : "";
          return msg ? (field && field !== "body" ? `${field}: ${msg}` : msg) : null;
        })
        .filter((s): s is string => Boolean(s));
      if (parts.length) return parts.join("; ");
    }
  }
  if (status === 413) return "That file is too large.";
  if (status === 415) return "Only PDF files are accepted.";
  if (status >= 500) return "The server had a problem. Please try again.";
  return `Request failed (${status}).`;
}
