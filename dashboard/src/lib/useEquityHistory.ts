"use client";

import { useEffect, useRef, useState } from "react";

const STORAGE_KEY = "hl-equity-history-v1";
const MAX_POINTS = 2000;
const MIN_INTERVAL_MS = 30_000; // sample at most every 30s unless equity moves

export interface EquityPoint {
  t: number;
  equity: number;
}

function loadStored(): EquityPoint[] {
  if (typeof window === "undefined") return [];
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw) as EquityPoint[];
    return Array.isArray(parsed) ? parsed.filter((p) => p.t > 0 && p.equity > 0) : [];
  } catch {
    return [];
  }
}

function saveStored(points: EquityPoint[]) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(points.slice(-MAX_POINTS)));
  } catch {
    /* quota exceeded — ignore */
  }
}

/** Build a time series of live wallet total equity from the webData2 stream. */
export function useEquityHistory(totalEquity: number | undefined | null) {
  const [points, setPoints] = useState<EquityPoint[]>([]);
  const lastSample = useRef<{ t: number; equity: number } | null>(null);
  const hydrated = useRef(false);

  useEffect(() => {
    if (!hydrated.current) {
      hydrated.current = true;
      setPoints(loadStored());
    }
  }, []);

  useEffect(() => {
    if (totalEquity == null || totalEquity <= 0 || !Number.isFinite(totalEquity)) return;
    const now = Date.now();
    const last = lastSample.current;
    const changed = !last || Math.abs(last.equity - totalEquity) > 0.005;
    const stale = !last || now - last.t >= MIN_INTERVAL_MS;
    if (!changed && !stale) return;

    lastSample.current = { t: now, equity: totalEquity };
    setPoints((prev) => {
      const tail = prev[prev.length - 1];
      if (tail && tail.t === now) return prev;
      const next = [...prev, { t: now, equity: totalEquity }];
      const capped = next.slice(-MAX_POINTS);
      saveStored(capped);
      return capped;
    });
  }, [totalEquity]);

  return points;
}
