import path from "node:path";
import fs from "node:fs";
import { spawn } from "node:child_process";
import { NextResponse } from "next/server";

// Spawning a local process is inherently a Node (not edge) concern, and the
// status must never be cached.
export const runtime = "nodejs";
export const dynamic = "force-dynamic";

// The bot lives at <repo>/bot; the dashboard runs from <repo>/dashboard.
// All paths are overridable for non-standard layouts / deploys.
function botDir(): string {
  return process.env.BOT_DIR || path.join(process.cwd(), "..", "bot");
}
function statusPath(): string {
  return path.join(botDir(), "data", "bot_status.json");
}
function logPath(): string {
  return path.join(botDir(), "data", "paper.log");
}
function pythonPath(): string {
  return process.env.BOT_PYTHON || path.join(botDir(), ".venv", "bin", "python");
}

// Heartbeat older than this => the engine likely crashed without cleanup.
const STALE_MS = 12_000;

interface StatusFile {
  pid?: number;
  state?: string;
  mode?: string;
  network?: string | null;
  strategy?: string | null;
  started_at?: string;
  updated_at?: string;
  equity?: number;
  open_positions?: number;
  pending_orders?: number;
  coins?: string[];
}

function readStatus(): StatusFile | null {
  try {
    const raw = fs.readFileSync(statusPath(), "utf8");
    return JSON.parse(raw) as StatusFile;
  } catch {
    return null;
  }
}

function pidAlive(pid: number | undefined): boolean {
  if (!pid) return false;
  try {
    process.kill(pid, 0); // signal 0 = liveness probe
    return true;
  } catch (err) {
    // EPERM => process exists but owned by another user; treat as alive.
    return (err as NodeJS.ErrnoException).code === "EPERM";
  }
}

function buildStatus() {
  const s = readStatus();
  const alive = pidAlive(s?.pid);
  const updatedMs = s?.updated_at ? Date.parse(s.updated_at) : 0;
  const stale = alive ? false : Boolean(s && s.state === "running");
  const fresh = updatedMs > 0 && Date.now() - updatedMs < STALE_MS;
  const running = alive && s?.state !== "stopped";

  let message: string | undefined;
  if (stale) message = "status says running but the process is gone (likely crashed)";
  else if (running && !fresh && s?.state === "running") message = "heartbeat is delayed";

  return {
    running,
    pid: running ? s?.pid ?? null : null,
    state: running ? s?.state ?? "running" : "stopped",
    mode: s?.mode ?? "live",
    network: s?.network ?? null,
    strategy: s?.strategy ?? null,
    startedAt: s?.started_at ?? null,
    updatedAt: s?.updated_at ?? null,
    equity: s?.equity ?? null,
    openPositions: s?.open_positions ?? null,
    pendingOrders: s?.pending_orders ?? null,
    coins: s?.coins ?? null,
    stale,
    message,
  };
}

export async function GET() {
  return NextResponse.json(buildStatus());
}

// Which engine the control starts. "live" places real orders on the venue in
// .env (testnet by default); "paper" runs the in-memory simulator.
const SCRIPTS: Record<string, string> = { live: "live.py", paper: "paper.py" };

export async function POST(req: Request) {
  let action = "";
  let script = "live";
  try {
    const body = (await req.json()) as { action?: string; script?: string };
    action = body.action ?? "";
    if (body.script && body.script in SCRIPTS) script = body.script;
  } catch {
    /* no body */
  }

  if (action === "start") {
    const current = buildStatus();
    if (current.running) {
      return NextResponse.json({ ...current, message: "already running" });
    }

    const py = pythonPath();
    if (!fs.existsSync(py)) {
      return NextResponse.json(
        { ...current, message: `python not found at ${py} , create the venv first` },
        { status: 400 },
      );
    }

    const dataDir = path.join(botDir(), "data");
    fs.mkdirSync(dataDir, { recursive: true });
    const out = fs.openSync(logPath(), "a");

    const child = spawn(py, [SCRIPTS[script]], {
      cwd: botDir(),
      detached: true,
      stdio: ["ignore", out, out],
    });
    child.unref();
    fs.closeSync(out);

    // Interim status so the UI shows "starting" until the engine's first
    // heartbeat (coin selection takes a few seconds before trading begins).
    const startedAt = new Date().toISOString();
    fs.writeFileSync(
      statusPath(),
      JSON.stringify(
        { pid: child.pid, state: "starting", mode: script, started_at: startedAt, updated_at: startedAt },
        null,
        2,
      ),
    );

    return NextResponse.json({ ...buildStatus(), state: "starting", running: true, pid: child.pid });
  }

  if (action === "stop") {
    const s = readStatus();
    if (!pidAlive(s?.pid)) {
      return NextResponse.json({ ...buildStatus(), message: "not running" });
    }
    try {
      process.kill(s!.pid!, "SIGTERM"); // paper.py handles this -> graceful shutdown
    } catch (err) {
      return NextResponse.json(
        { ...buildStatus(), message: `failed to stop: ${String(err)}` },
        { status: 500 },
      );
    }
    // Reflect intent immediately; the bot writes "stopped" once it has flushed.
    if (s) {
      fs.writeFileSync(
        statusPath(),
        JSON.stringify({ ...s, state: "stopping", updated_at: new Date().toISOString() }, null, 2),
      );
    }
    return NextResponse.json({ ...buildStatus(), state: "stopping" });
  }

  return NextResponse.json({ error: "unknown action; use 'start' or 'stop'" }, { status: 400 });
}
