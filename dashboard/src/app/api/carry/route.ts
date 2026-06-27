import path from "node:path";
import fs from "node:fs";
import { NextResponse } from "next/server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const STALE_MS = 15_000;

function botDir(): string {
  return process.env.BOT_DIR || path.join(process.cwd(), "..", "bot");
}

function carryStatusPath(): string {
  return path.join(botDir(), "data", "carry_status.json");
}

function pidAlive(pid: number | undefined): boolean {
  if (!pid) return false;
  try {
    process.kill(pid, 0);
    return true;
  } catch (err) {
    return (err as NodeJS.ErrnoException).code === "EPERM";
  }
}

interface CarryStatusFile {
  pid?: number;
  state?: string;
  mode?: string;
  strategy?: string;
  network?: string;
  wallet_network?: string;
  capital?: number;
  total_equity?: number;
  paper_mode?: boolean;
  equity?: number;
  pnl?: number;
  deployed?: number;
  deploy_pct?: number;
  blended_apr_pct?: number;
  net_apr_30d_pct?: number;
  entry_cost_paid?: number;
  funding_earned?: number;
  breakeven_pct?: number;
  rebalance_minutes?: number;
  open_positions?: number;
  coins?: string[];
  positions?: {
    coin: string;
    side: string;
    notional: number;
    accrued: number;
    funding_apr_pct: number;
    breakeven_days: number | null;
  }[];
  started_at?: string;
  updated_at?: string;
}

export async function GET() {
  const file = carryStatusPath();
  if (!fs.existsSync(file)) {
    return NextResponse.json({
      ready: false,
      reason: "carry allocator not started, run: cd bot && ./.venv/bin/python carry.py paper",
      running: false,
    });
  }

  try {
    const raw = fs.readFileSync(file, "utf8");
    const s = JSON.parse(raw) as CarryStatusFile;
    const alive = pidAlive(s.pid);
    const updatedMs = s.updated_at ? Date.parse(s.updated_at) : 0;
    const fresh = updatedMs > 0 && Date.now() - updatedMs < STALE_MS;
    const running = alive && s.state !== "stopped";

    let message: string | undefined;
    if (s.state === "running" && !alive) message = "carry process is gone (likely crashed)";
    else if (running && !fresh) message = "carry heartbeat is delayed";

    return NextResponse.json({
      ready: true,
      running,
      stale: Boolean(s.state === "running" && !alive),
      fresh,
      message,
      pid: running ? s.pid ?? null : null,
      state: running ? s.state ?? "running" : "stopped",
      mode: s.mode ?? "paper",
      strategy: s.strategy ?? "carry_v1",
      network: s.network ?? null,
      capital: s.total_equity ?? s.capital ?? null,
      totalEquity: s.total_equity ?? s.capital ?? null,
      paperMode: s.paper_mode ?? true,
      equity: s.equity ?? null,
      pnl: s.pnl ?? null,
      deployed: s.deployed ?? null,
      deployPct: s.deploy_pct ?? null,
      blendedAprPct: s.blended_apr_pct ?? null,
      netApr30dPct: s.net_apr_30d_pct ?? null,
      entryCostPaid: s.entry_cost_paid ?? null,
      fundingEarned: s.funding_earned ?? null,
      breakevenPct: s.breakeven_pct ?? null,
      rebalanceMinutes: s.rebalance_minutes ?? null,
      openPositions: s.open_positions ?? 0,
      coins: s.coins ?? [],
      positions: s.positions ?? [],
      startedAt: s.started_at ?? null,
      updatedAt: s.updated_at ?? null,
    });
  } catch (err) {
    return NextResponse.json(
      { ready: false, reason: String(err), running: false },
      { status: 500 },
    );
  }
}
