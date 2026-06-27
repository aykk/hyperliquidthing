import type { ReactNode } from "react";
import type { ConnectionStatus } from "@/lib/types";
import { fmtTime, shortAddr } from "@/lib/format";

const STATUS_META: Record<ConnectionStatus, { label: string; dot: string; text: string }> = {
  connected: { label: "live", dot: "bg-emerald-500", text: "text-emerald-400" },
  connecting: { label: "connecting", dot: "bg-amber-500 animate-pulse", text: "text-amber-400" },
  disconnected: { label: "reconnecting…", dot: "bg-red-500 animate-pulse", text: "text-red-400" },
};

export function Header({
  status,
  address,
  lastUpdate,
  title = "live portfolio",
  network,
  children,
}: {
  status: ConnectionStatus;
  address: string;
  lastUpdate: number | null;
  title?: string;
  network?: string;
  children?: ReactNode;
}) {
  const meta = STATUS_META[status];
  return (
    <header className="flex flex-wrap items-center justify-between gap-3 border-b border-zinc-800 pb-4">
      <div>
        <h1 className="text-lg font-semibold tracking-tight text-zinc-100">{title}</h1>
        <p className="font-mono text-xs text-zinc-500">
          {address ? shortAddr(address) : "no address configured"}
          {network ? <span className="ml-2 text-zinc-600">· {network}</span> : null}
        </p>
      </div>
      <div className="flex items-center gap-4">
        {children}
        {lastUpdate && (
          <span className="font-mono text-xs text-zinc-600">updated {fmtTime(lastUpdate)}</span>
        )}
        <div className="flex items-center gap-2 rounded-full border border-zinc-800 bg-zinc-900 px-3 py-1.5">
          <span className={`h-2 w-2 rounded-full ${meta.dot}`} />
          <span className={`text-xs font-medium ${meta.text}`}>{meta.label}</span>
        </div>
      </div>
    </header>
  );
}
