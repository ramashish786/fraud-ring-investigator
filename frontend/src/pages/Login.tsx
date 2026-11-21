import { useState, type FormEvent } from "react";
import { useAuth } from "../auth";
import { ErrorNote } from "../components/ui";

export function Login() {
  const { login } = useAuth();
  const [email, setEmail] = useState("analyst@bank.test");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    try { await login(email, password); } catch (err) { setError((err as Error).message); } finally { setBusy(false); }
  };
  return (
    <main className="login">
      <form onSubmit={submit} className="card">
        <h1>Fraud Ring Investigator</h1>
        <p className="muted">Synthetic data only. Seeded users: analyst@, analyst2@, reviewer@, auditor@, admin@bank.test</p>
        <label>Email<input value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="username" /></label>
        <label>Password<input type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" /></label>
        <ErrorNote error={error} />
        <button disabled={busy}>{busy ? "Signing in…" : "Sign in"}</button>
      </form>
    </main>
  );
}
