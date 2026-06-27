"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { CarryStatus } from "@/lib/types";

const POLL_MS = 5000;

export function useCarryData(enabled = true) {
  const [data, setData] = useState<CarryStatus | null>(null);
  const [lastUpdate, setLastUpdate] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);

  const refresh = useCallback(async () => {
    if (!enabled) return;
    try {
      const res = await fetch("/api/carry", { cache: "no-store" });
      const json = (await res.json()) as CarryStatus;
      setData(json);
      setLastUpdate(Date.now());
      setError(null);
    } catch (err) {
      setError(String(err));
    }
  }, [enabled]);

  useEffect(() => {
    if (!enabled) return;
    refresh();
    timer.current = setInterval(refresh, POLL_MS);
    return () => {
      if (timer.current) clearInterval(timer.current);
    };
  }, [enabled, refresh]);

  return { data, lastUpdate, error, refresh };
}
