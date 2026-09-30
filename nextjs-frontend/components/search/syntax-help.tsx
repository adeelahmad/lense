import { cn } from "@/lib/utils";

/** The backend's search rules, as the "?" popover and the start page explain them. */
export const SYNTAX: [string, string][] = [
  ["red teaming", "both words, anywhere in a segment"],
  ["exploit", "also finds exploits, exploited (English stemming)"],
  ['"system card"', "the exact phrase"],
  ["cyber OR bio", "either word"],
  ['speaker:"Host B"', "only what Host B said"],
  ["emotion:surprise", "segments tagged Surprise"],
  ["namespace:podcasts", "one namespace"],
  ['recording:"Episode 12"', "one recording"],
];

export function SyntaxHelp({ className, onPick }: { className?: string; onPick?: (example: string) => void }) {
  return (
    <div className={cn("flex flex-col gap-2.5", className)}>
      <h2 className="text-[14px] font-bold text-fg">How search works</h2>
      <dl className="m-0 grid grid-cols-[minmax(0,150px)_1fr] gap-x-2.5 gap-y-2 text-[13px] leading-snug">
        {SYNTAX.map(([k, v]) => (
          <div key={k} className="contents">
            <dt>
              {onPick ? (
                <button type="button" onClick={() => onPick(k)} className="text-left font-mono text-[12.5px] font-medium text-fg-accent hover:underline">
                  {k}
                </button>
              ) : (
                <code className="font-mono text-[12.5px] font-medium text-fg-accent">{k}</code>
              )}
            </dt>
            <dd className="m-0 text-fg-strong">{v}</dd>
          </div>
        ))}
      </dl>
      <p className="m-0 border-t border-border pt-2 text-[12px] leading-snug text-fg-muted">
        No prefix search: <code className="font-mono">interp*</code> won’t work — type the whole word. Press <kbd className="font-sans">/</kbd> to focus search.
      </p>
    </div>
  );
}
