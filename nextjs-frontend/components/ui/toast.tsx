"use client";

import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react";

type Tone = "intent" | "green" | "red" | "gate";
export type ToastInput = { title: ReactNode; body?: ReactNode; tone?: Tone; action?: { label: string; onClick: () => void }; duration?: number };
type ToastItem = ToastInput & { id: number };

const GLYPH: Record<Tone, [string, string]> = {
  intent: ["●", "var(--term-blue)"],
  green: ["✓", "var(--term-green)"],
  red: ["✕", "var(--term-red)"],
  gate: ["◆", "var(--term-gold)"],
};

const Ctx = createContext<(t: ToastInput) => void>(() => undefined);

/** Dark toasts, bottom-left, with an optional action (Undo, Retry, Open). */
export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([]);
  const dismiss = useCallback((id: number) => setItems((xs) => xs.filter((x) => x.id !== id)), []);
  const push = useCallback(
    (t: ToastInput) => {
      const id = Date.now() + Math.random();
      setItems((xs) => [...xs.slice(-3), { ...t, id }]);
      setTimeout(() => dismiss(id), t.duration ?? (t.tone === "red" ? 8000 : 5000));
    },
    [dismiss],
  );
  const value = useMemo(() => push, [push]);
  return (
    <Ctx.Provider value={value}>
      {children}
      <div aria-live="polite" className="pointer-events-none fixed bottom-4 left-4 z-[300] flex flex-col gap-2">
        {items.map((t) => {
          const [g, c] = GLYPH[t.tone ?? "intent"];
          return (
            <div
              key={t.id}
              role="status"
              className="pointer-events-auto flex min-w-[280px] max-w-[440px] items-center gap-3 rounded-md bg-[#202124] px-4 py-3 text-[14px] leading-snug text-white shadow-3 animate-fade-in"
            >
              <span aria-hidden style={{ color: c }} className="shrink-0 text-[14px]">
                {g}
              </span>
              <div className="min-w-0 flex-1">
                <div className="font-bold">{t.title}</div>
                {t.body && <div className="text-[13px] text-[var(--term-muted)]">{t.body}</div>}
              </div>
              {t.action && (
                <button
                  type="button"
                  className="shrink-0 font-bold text-[var(--term-blue)] hover:underline"
                  onClick={() => {
                    t.action?.onClick();
                    dismiss(t.id);
                  }}
                >
                  {t.action.label}
                </button>
              )}
            </div>
          );
        })}
      </div>
    </Ctx.Provider>
  );
}

export function useToast() {
  return useContext(Ctx);
}
