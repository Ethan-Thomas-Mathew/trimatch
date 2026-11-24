import { NavLink, Outlet, useNavigate } from "react-router-dom";

import { clearCredentials } from "../lib/auth.ts";

const link = ({ isActive }: { isActive: boolean }) =>
  `rounded-md px-3 py-1.5 text-sm font-medium ${
    isActive ? "bg-slate-900 text-white" : "text-slate-700 hover:bg-slate-200"
  }`;

export function Layout() {
  const navigate = useNavigate();
  return (
    <div className="min-h-screen">
      <header className="border-b border-slate-200 bg-white">
        <div className="mx-auto flex max-w-7xl items-center gap-4 px-4 py-3">
          <span className="text-lg font-semibold tracking-tight">TriMatch</span>
          <nav className="flex gap-1" aria-label="Main">
            <NavLink to="/" end className={link}>
              Inbox
            </NavLink>
            <NavLink to="/metrics" className={link}>
              Metrics
            </NavLink>
          </nav>
          <button
            className="btn-ghost ml-auto"
            onClick={() => {
              clearCredentials();
              navigate("/login");
            }}
          >
            Sign out
          </button>
        </div>
      </header>
      <main className="mx-auto max-w-7xl px-4 py-6">
        <Outlet />
      </main>
    </div>
  );
}
