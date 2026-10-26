import { useCallback, useEffect, useState } from "react";
import { ApiError } from "./api";

export interface Loaded<T> {
  data: T | null;
  error: string | null;
  loading: boolean;
  reload: () => void;
}

/** Load on mount; keep the error (with a retry) instead of silently showing "nothing". */
export function useLoad<T>(load: () => Promise<T>): Loaded<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const fetch = useCallback(() => {
    load().then(
      (value) => {
        setData(value);
        setError(null);
        setLoading(false);
      },
      (err: unknown) => {
        setError(err instanceof ApiError ? err.message : "the app didn't answer");
        setLoading(false);
      },
    );
  }, [load]);
  useEffect(fetch, [fetch]);
  const reload = useCallback(() => {
    setLoading(true);
    fetch();
  }, [fetch]);
  return { data, error, loading, reload };
}
