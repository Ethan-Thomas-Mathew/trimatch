import { FormEvent, useState } from "react";
import { useNavigate } from "react-router-dom";

import { api } from "../api.ts";
import { clearCredentials, saveCredentials } from "../lib/auth.ts";

export function Login() {
  const navigate = useNavigate();
  const [username, setUsername] = useState("reviewer");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    saveCredentials(username, password);
    try {
      await api.metrics(); // any authenticated call proves the credentials work
      navigate("/");
    } catch (err) {
      clearCredentials();
      setError(err instanceof Error ? err.message : "Sign-in failed.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center px-4">
      <form onSubmit={submit} className="card w-full max-w-sm space-y-4 p-6">
        <h1 className="text-xl font-semibold">TriMatch reviewer sign-in</h1>
        <div>
          <label htmlFor="username" className="block text-sm font-medium">
            Username
          </label>
          <input
            id="username"
            className="mt-1 w-full rounded-md border border-slate-300 px-3 py-2"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            autoComplete="username"
            required
          />
        </div>
        <div>
          <label htmlFor="password" className="block text-sm font-medium">
            Password
          </label>
          <input
            id="password"
            type="password"
            className="mt-1 w-full rounded-md border border-slate-300 px-3 py-2"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete="current-password"
            required
          />
          <p className="mt-1 text-xs text-slate-500">
            The password is printed in the backend log on start-up unless REVIEWER_PASSWORD is set.
          </p>
        </div>
        <div role="alert" aria-live="polite" className="min-h-5 text-sm text-rose-700">
          {error}
        </div>
        <button className="btn-primary w-full" disabled={busy}>
          {busy ? "Signing in…" : "Sign in"}
        </button>
      </form>
    </div>
  );
}
