"use client";

import { useCallback, useEffect, useRef, useState } from "react";

/** Fetch now and every `intervalMs` while `active` is true. */
export function usePoll<T>(fn: () => Promise<T>, intervalMs = 3000, active = true) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const fnRef = useRef(fn);
  fnRef.current = fn;

  const refresh = useCallback(async () => {
    try {
      setData(await fnRef.current());
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    void refresh();
    if (!active) return;
    const id = setInterval(() => void refresh(), intervalMs);
    return () => clearInterval(id);
  }, [refresh, intervalMs, active]);

  return { data, error, refresh };
}
