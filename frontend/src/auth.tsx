import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { api, token } from "./api";
import type { Me, Role } from "./types";

interface AuthState { me: Me | null; ready: boolean; login: (e: string, p: string) => Promise<void>; logout: () => void; can: (...r: Role[]) => boolean }
const Ctx = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [me, setMe] = useState<Me | null>(null);
  const [ready, setReady] = useState(false);
  const logout = useCallback(() => { token.clear(); setMe(null); }, []);
  useEffect(() => {
    const onLogout = () => setMe(null);
    window.addEventListener("fri:logout", onLogout);
    (token.get() ? api.me().then(setMe).catch(logout) : Promise.resolve()).finally(() => setReady(true));
    return () => window.removeEventListener("fri:logout", onLogout);
  }, [logout]);
  const login = async (email: string, password: string) => {
    const r = await api.login(email, password);
    token.set(r.access_token);
    setMe(await api.me());
  };
  const can = (...roles: Role[]) => !!me && (me.roles.includes("admin") || roles.some((r) => me.roles.includes(r)));
  return <Ctx.Provider value={{ me, ready, login, logout, can }}>{children}</Ctx.Provider>;
}

export function useAuth(): AuthState {
  const v = useContext(Ctx);
  if (!v) throw new Error("useAuth outside AuthProvider");
  return v;
}
