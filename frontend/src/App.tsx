import { Navigate, Route, Routes } from "react-router-dom";

import { Layout } from "./components/Layout.tsx";
import { getAuthHeader } from "./lib/auth.ts";
import { Detail } from "./pages/Detail.tsx";
import { Inbox } from "./pages/Inbox.tsx";
import { Login } from "./pages/Login.tsx";
import { MetricsPage } from "./pages/MetricsPage.tsx";

function RequireLogin({ children }: { children: JSX.Element }) {
  return getAuthHeader() ? children : <Navigate to="/login" replace />;
}

export function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route
        element={
          <RequireLogin>
            <Layout />
          </RequireLogin>
        }
      >
        <Route index element={<Inbox />} />
        <Route path="invoices/:id" element={<Detail />} />
        <Route path="metrics" element={<MetricsPage />} />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
