"use client";

import { useEffect, useRef, useState } from "react";
import type {
  AccountSummary,
  ConnectionStatus,
  Fill,
  OpenOrder,
  Position,
} from "./types";

const IS_MAINNET = process.env.NEXT_PUBLIC_IS_MAINNET !== "false";
const WS_URL = IS_MAINNET
  ? "wss://api.hyperliquid.xyz/ws"
  : "wss://api.hyperliquid-testnet.xyz/ws";
const ADDRESS = (process.env.NEXT_PUBLIC_MAIN_ACCOUNT_ADDRESS || "").toLowerCase();

const RECONNECT_MS = 3000; // spec: reconnect every 3s
const PING_MS = 30000;
const MAX_FILLS = 20;

function num(v: unknown): number {
  const n = typeof v === "string" ? parseFloat(v) : (v as number);
  return Number.isFinite(n) ? n : 0;
}

export interface DashboardData {
  status: ConnectionStatus;
  lastUpdate: number | null;
  account: AccountSummary | null;
  positions: Position[];
  openOrders: OpenOrder[];
  fills: Fill[];
  address: string;
}

function parseWebData2(d: any): {
  account: AccountSummary;
  positions: Position[];
  openOrders: OpenOrder[];
} {
  const chs = d?.clearinghouseState ?? {};
  const ms = chs?.marginSummary ?? {};
  const perpsAccountValue = num(ms.accountValue);
  const marginUsed = num(ms.totalMarginUsed);

  // Mark-price map from parallel meta.universe / assetCtxs arrays.
  const universe: any[] = d?.meta?.universe ?? [];
  const ctxs: any[] = d?.assetCtxs ?? [];
  const markByCoin: Record<string, number> = {};
  universe.forEach((u, i) => {
    if (u?.name && ctxs[i]?.markPx) markByCoin[u.name] = num(ctxs[i].markPx);
  });

  // Spot balances (unified account holds usable collateral here).
  const spotBalances: any[] = d?.spotState?.balances ?? [];
  let spotUsdc = 0;
  let spotUsdcHold = 0;
  for (const b of spotBalances) {
    if (b?.coin === "USDC") {
      spotUsdc = num(b.total);
      spotUsdcHold = num(b.hold);
    }
  }

  const positions: Position[] = (chs?.assetPositions ?? [])
    .map((ap: any) => {
      const p = ap?.position ?? {};
      return {
        coin: p.coin,
        szi: num(p.szi),
        entryPx: p.entryPx != null ? num(p.entryPx) : null,
        notionalUsd: num(p.positionValue),
        liquidationPx: p.liquidationPx != null ? num(p.liquidationPx) : null,
        unrealizedPnl: num(p.unrealizedPnl),
        marginUsed: num(p.marginUsed),
        markPx: markByCoin[p.coin] ?? null,
      } as Position;
    })
    .filter((p: Position) => p.szi !== 0);

  const openOrders: OpenOrder[] = (d?.openOrders ?? []).map((o: any) => ({
    coin: o.coin,
    side: o.side,
    limitPx: num(o.limitPx),
    sz: num(o.sz),
    oid: o.oid,
    timestamp: o.timestamp,
  }));

  // Unified-account equity. IMPORTANT: webData2 reports balances differently from
  // the REST spot_user_state endpoint (bot/monitor.py). In webData2 a position's
  // margin is moved OUT of the spot balance and INTO the perps accountValue
  // (spotUsdc reads net-of-margin, perps holds margin + uPnL). So here the
  // correct total equity is perps + spot, they do NOT double-count. (The REST
  // endpoint keeps margin in spot as "hold" and duplicates it in perps, so there
  // the correct figure is spot + uPnL; same number, different decomposition.)
  const totalEquity = perpsAccountValue + spotUsdc;
  const availableMargin = Math.max(0, spotUsdc - spotUsdcHold) + num(chs?.withdrawable);
  const marginUsedPct = totalEquity > 0 ? Math.min(100, (marginUsed / totalEquity) * 100) : 0;

  return {
    account: {
      totalEquity,
      availableMargin,
      marginUsedPct,
      marginUsed,
      spotUsdc,
      perpsAccountValue,
    },
    positions,
    openOrders,
  };
}

export function useHyperliquidData(): DashboardData {
  const [status, setStatus] = useState<ConnectionStatus>("connecting");
  const [lastUpdate, setLastUpdate] = useState<number | null>(null);
  const [account, setAccount] = useState<AccountSummary | null>(null);
  const [positions, setPositions] = useState<Position[]>([]);
  const [openOrders, setOpenOrders] = useState<OpenOrder[]>([]);
  const [fills, setFills] = useState<Fill[]>([]);

  const wsRef = useRef<WebSocket | null>(null);
  const reconnectRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pingRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const closedRef = useRef(false);

  useEffect(() => {
    closedRef.current = false;

    function connect() {
      if (!ADDRESS) {
        setStatus("disconnected");
        return;
      }
      setStatus("connecting");
      const ws = new WebSocket(WS_URL);
      wsRef.current = ws;

      ws.onopen = () => {
        setStatus("connected");
        ws.send(
          JSON.stringify({
            method: "subscribe",
            subscription: { type: "webData2", user: ADDRESS },
          }),
        );
        ws.send(
          JSON.stringify({
            method: "subscribe",
            subscription: { type: "userFills", user: ADDRESS },
          }),
        );
        pingRef.current = setInterval(() => {
          if (ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({ method: "ping" }));
          }
        }, PING_MS);
      };

      ws.onmessage = (event) => {
        let msg: any;
        try {
          msg = JSON.parse(event.data);
        } catch {
          return;
        }
        if (msg.channel === "webData2") {
          const parsed = parseWebData2(msg.data);
          setAccount(parsed.account);
          setPositions(parsed.positions);
          setOpenOrders(parsed.openOrders);
          setLastUpdate(Date.now());
        } else if (msg.channel === "userFills") {
          const incoming: Fill[] = (msg.data?.fills ?? []).map((f: any) => ({
            coin: f.coin,
            side: f.side,
            px: num(f.px),
            sz: num(f.sz),
            time: f.time,
            closedPnl: num(f.closedPnl),
            dir: f.dir ?? "",
            tid: f.tid,
          }));
          if (incoming.length) {
            setFills((prev) => {
              // De-duplicate by fill id (tid). The userFills stream re-sends a
              // snapshot of recent fills on every (re)connect, so without this
              // the same fills get appended repeatedly and inflate the history.
              const seen = new Set<string>();
              const merged: Fill[] = [];
              for (const f of [...incoming.reverse(), ...prev]) {
                const key =
                  f.tid != null
                    ? `tid:${f.tid}`
                    : `${f.time}-${f.coin}-${f.px}-${f.sz}-${f.dir}`;
                if (seen.has(key)) continue;
                seen.add(key);
                merged.push(f);
              }
              return merged.slice(0, MAX_FILLS);
            });
            setLastUpdate(Date.now());
          }
        }
      };

      ws.onclose = () => {
        if (pingRef.current) clearInterval(pingRef.current);
        if (closedRef.current) return;
        setStatus("disconnected");
        reconnectRef.current = setTimeout(connect, RECONNECT_MS);
      };

      ws.onerror = () => {
        ws.close();
      };
    }

    connect();

    return () => {
      closedRef.current = true;
      if (reconnectRef.current) clearTimeout(reconnectRef.current);
      if (pingRef.current) clearInterval(pingRef.current);
      wsRef.current?.close();
    };
  }, []);

  return {
    status,
    lastUpdate,
    account,
    positions,
    openOrders,
    fills,
    address: ADDRESS,
  };
}
