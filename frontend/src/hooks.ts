import { useCallback, useEffect, useRef, useState } from "react";

/** Load data, optionally re-polling every `intervalMs` while `poll` is true. */
export function useData<T>(load: () => Promise<T>, deps: unknown[], opts: { poll?: boolean; intervalMs?: number } = {}) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const loadRef = useRef(load);
  loadRef.current = load;
  const refresh = useCallback(async () => {
    try { setData(await loadRef.current()); setError(null); } catch (e) { setError((e as Error).message); }
  }, []);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { setData(null); void refresh(); }, deps);
  useEffect(() => {
    if (!opts.poll) return;
    const t = setInterval(() => void refresh(), opts.intervalMs ?? 3000);
    return () => clearInterval(t);
  }, [opts.poll, opts.intervalMs, refresh]);
  return { data, error, refresh };
}
