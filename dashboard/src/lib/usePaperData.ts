"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { PaperResponse } from "./types";

const POLL_MS = 4000;

// Paper trades live in a local SQLite journal, not on a websocket. We poll the
// /api/paper route, which reads the DB the Python paper engine writes to.
export function usePaperData(active: boolean) {
  const [data, setData] = useState<PaperResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [lastUpdate, setLastUpdate] = useState<number | null>(null);
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);

  const fetchOnce = useCallback(async () => {
    try {
      setLoading(true);
      const res = await fetch("/api/paper", { cache: "no-store" });
      const json = (await res.json()) as PaperResponse;
      setData(json);
      setLastUpdate(Date.now());
    } catch (err) {
      setData({
        ready: false,
        reason: String(err),
        stats: { closed_trades: 0 },
        trades: [],
      });
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!active) {
      if (timer.current) clearInterval(timer.current);
      timer.current = null;
      return;
    }
    fetchOnce();
    timer.current = setInterval(fetchOnce, POLL_MS);
    return () => {
      if (timer.current) clearInterval(timer.current);
      timer.current = null;
    };
  }, [active, fetchOnce]);

  return { data, loading, lastUpdate, refetch: fetchOnce };
}
