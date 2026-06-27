"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { BotStatus } from "@/lib/types";

const POLL_MS = 3000;

export function useBotControl() {
  const [status, setStatus] = useState<BotStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);

  const refresh = useCallback(async () => {
    try {
      const res = await fetch("/api/bot", { cache: "no-store" });
      const data = (await res.json()) as BotStatus;
      setStatus(data);
      setError(null);
    } catch (err) {
      setError(String(err));
    }
  }, []);

  const send = useCallback(
    async (action: "start" | "stop") => {
      setBusy(true);
      try {
        const res = await fetch("/api/bot", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ action }),
        });
        const data = (await res.json()) as BotStatus;
        setStatus(data);
        setError(null);
      } catch (err) {
        setError(String(err));
      } finally {
        setBusy(false);
        // Re-poll shortly after to pick up the engine's first heartbeat / exit.
        setTimeout(refresh, 1200);
      }
    },
    [refresh],
  );

  useEffect(() => {
    refresh();
    timer.current = setInterval(refresh, POLL_MS);
    return () => {
      if (timer.current) clearInterval(timer.current);
    };
  }, [refresh]);

  return {
    status,
    error,
    busy,
    start: () => send("start"),
    stop: () => send("stop"),
  };
}
