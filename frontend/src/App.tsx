import { NavLink, Navigate, Route, Routes } from "react-router-dom";
import { useAuth } from "./auth";
import { Alerts } from "./pages/Alerts";
import { Dashboard } from "./pages/Dashboard";
import { Documents } from "./pages/Documents";
import { InvestigationDetail } from "./pages/InvestigationDetail";
import { Investigations } from "./pages/Investigations";
import { Login } from "./pages/Login";

export function App() {
  const { me, ready, logout } = useAuth();
  if (!ready) return <p className="center">Loading…</p>;
  if (!me) return <Login />;
  return (
    <div className="shell">
      <header className="topbar">
        <strong>Fraud Ring Investigator</strong>
        <nav>
          <NavLink to="/" end>Dashboard</NavLink>
          <NavLink to="/alerts">Alerts</NavLink>
          <NavLink to="/investigations">Investigations</NavLink>
          <NavLink to="/documents">Documents</NavLink>
        </nav>
        <span className="who">{me.roles.join(", ")} · clearance {me.clearance} <button className="link" onClick={logout}>Sign out</button></span>
      </header>
      <main>
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/alerts" element={<Alerts />} />
          <Route path="/investigations" element={<Investigations />} />
          <Route path="/investigations/:id" element={<InvestigationDetail />} />
          <Route path="/documents" element={<Documents />} />
          <Route path="*" element={<Navigate to="/" />} />
        </Routes>
      </main>
    </div>
  );
}
