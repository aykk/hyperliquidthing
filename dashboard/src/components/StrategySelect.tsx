"use client";

/** Segmented control to filter journal analytics by strategy ("all" + each). */
export function StrategySelect({
  strategies,
  value,
  onChange,
}: {
  strategies: string[];
  value: string;
  onChange: (v: string) => void;
}) {
  const options = ["all", ...strategies];
  return (
    <div className="flex flex-wrap gap-1 rounded-lg border border-zinc-800 bg-zinc-900/60 p-1">
      {options.map((opt) => {
        const active = opt === value;
        return (
          <button
            key={opt}
            type="button"
            onClick={() => onChange(opt)}
            className={`rounded-md px-2.5 py-1 font-mono text-[11px] lowercase transition-colors ${
              active
                ? "bg-zinc-100 text-zinc-900"
                : "text-zinc-400 hover:bg-zinc-800 hover:text-zinc-200"
            }`}
          >
            {opt}
          </button>
        );
      })}
    </div>
  );
}
