export type ConnectionStatus = "connecting" | "connected" | "disconnected";

export interface Position {
  coin: string;
  szi: number;
  entryPx: number | null;
  notionalUsd: number;
  liquidationPx: number | null;
  unrealizedPnl: number;
  marginUsed: number;
  markPx: number | null;
}

export interface OpenOrder {
  coin: string;
  side: "B" | "A";
  limitPx: number;
  sz: number;
  oid: number;
  timestamp: number;
}

export interface Fill {
  coin: string;
  side: "B" | "A";
  px: number;
  sz: number;
  time: number;
  closedPnl: number;
  dir: string;
  tid?: number; // unique fill id (used to de-duplicate snapshot vs incremental)
}

export interface AccountSummary {
  totalEquity: number;
  availableMargin: number;
  marginUsedPct: number;
  marginUsed: number;
  spotUsdc: number;
  perpsAccountValue: number;
}

export type DashboardMode = "live" | "paper";

export interface PaperTrade {
  id: number;
  mode: string;
  strategy: string;
  coin: string;
  side: string;
  signal: string;
  status: string;
  entry_time: string;
  entry_px: number;
  size: number;
  notional: number;
  bid_ratio_entry: number | null;
  spread_pct_entry: number | null;
  funding_entry: number | null;
  sl_px: number | null;
  tp_px: number | null;
  exit_time: string | null;
  exit_px: number | null;
  exit_reason: string | null;
  fees: number | null;
  realized_pnl: number | null;
  return_pct: number | null;
  equity_after: number | null;
}

export interface PaperStats {
  closed_trades: number;
  wins?: number;
  losses?: number;
  win_rate_pct?: number;
  total_pnl?: number;
  total_fees?: number;
  avg_pnl?: number;
  avg_win?: number;
  avg_loss?: number;
  expectancy?: number;
  max_drawdown?: number;
  by_exit_reason?: Record<string, number>;
  pnl_by_coin?: Record<string, number>;
  pnl_by_strategy?: Record<string, number>;
}

export interface BotStatus {
  running: boolean;
  pid: number | null;
  state: string;
  mode: string;
  network?: string | null;
  strategy: string | null;
  startedAt: string | null;
  updatedAt: string | null;
  equity: number | null;
  openPositions: number | null;
  pendingOrders: number | null;
  coins: string[] | null;
  stale: boolean;
  message?: string;
}

export interface PaperResponse {
  ready: boolean;
  reason?: string;
  stats: PaperStats;
  openCount?: number;
  trades: PaperTrade[];
}

export interface CarryPositionDetail {
  coin: string;
  side: string;
  notional: number;
  accrued: number;
  funding_apr_pct: number;
  breakeven_days: number | null;
}

export interface CarryStatus {
  ready: boolean;
  reason?: string;
  running: boolean;
  stale?: boolean;
  fresh?: boolean;
  message?: string;
  pid: number | null;
  state: string;
  mode: string;
  strategy: string;
  network: string | null;
  capital: number | null;
  totalEquity: number | null;
  paperMode?: boolean;
  equity: number | null;
  pnl: number | null;
  deployed: number | null;
  deployPct: number | null;
  blendedAprPct: number | null;
  netApr30dPct: number | null;
  entryCostPaid: number | null;
  fundingEarned: number | null;
  breakevenPct: number | null;
  rebalanceMinutes: number | null;
  openPositions: number;
  coins: string[];
  positions: CarryPositionDetail[];
  startedAt: string | null;
  updatedAt: string | null;
}
