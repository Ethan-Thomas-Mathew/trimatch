// Credentials live in sessionStorage: cleared when the tab closes, never sent anywhere except
// the same-origin /api, and never written to localStorage.

const KEY = "trimatch.auth";

/** Basic auth header value. UTF-8 safe (btoa alone fails on non-Latin-1 characters). */
export function basicAuthHeader(username: string, password: string): string {
  const bytes = new TextEncoder().encode(`${username}:${password}`);
  let binary = "";
  for (const b of bytes) binary += String.fromCharCode(b);
  return "Basic " + btoa(binary);
}

export function saveCredentials(username: string, password: string): void {
  try {
    sessionStorage.setItem(KEY, basicAuthHeader(username, password));
  } catch {
    // Storage can be blocked (private mode); the in-memory copy below still works for this tab.
  }
  memory = basicAuthHeader(username, password);
}

let memory: string | null = null;

export function getAuthHeader(): string | null {
  if (memory) return memory;
  try {
    return sessionStorage.getItem(KEY);
  } catch {
    return null;
  }
}

export function clearCredentials(): void {
  memory = null;
  try {
    sessionStorage.removeItem(KEY);
  } catch {
    // nothing to clear
  }
}
