export function fmtUsd(n: number | null | undefined, dp = 2): string {
  if (n === null || n === undefined || Number.isNaN(n)) return ",";
  const sign = n < 0 ? "-" : "";
  return `${sign}$${Math.abs(n).toLocaleString("en-US", {
    minimumFractionDigits: dp,
    maximumFractionDigits: dp,
  })}`;
}

export function fmtNum(n: number | null | undefined, dp = 4): string {
  if (n === null || n === undefined || Number.isNaN(n)) return ",";
  return n.toLocaleString("en-US", { maximumFractionDigits: dp });
}

export function fmtPct(n: number | null | undefined, dp = 2): string {
  if (n === null || n === undefined || Number.isNaN(n)) return ",";
  return `${n.toFixed(dp)}%`;
}

export function fmtPx(n: number | null | undefined): string {
  if (n === null || n === undefined || Number.isNaN(n)) return ",";
  // 5 significant figures, Hyperliquid-style
  return n.toLocaleString("en-US", { maximumFractionDigits: 6 });
}

export function fmtTime(ms: number): string {
  return new Date(ms).toLocaleTimeString("en-US", { hour12: false });
}

export function fmtDateTime(iso: string | null | undefined): string {
  if (!iso) return ",";
  const ms = Date.parse(iso);
  if (Number.isNaN(ms)) return ",";
  return new Date(ms).toLocaleString("en-US", {
    year: "2-digit",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  });
}

export function shortAddr(addr: string): string {
  if (!addr || addr.length < 10) return addr;
  return `${addr.slice(0, 6)}…${addr.slice(-4)}`;
}
