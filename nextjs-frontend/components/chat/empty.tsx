"use client";

import { Settings } from "lucide-react";
import Link from "next/link";

import { scopeWords, type Scope } from "@/components/chat/scope";
import { Button } from "@/components/ui/button";

/** CH2: before the first question — what chat answers from, suggested questions, or why it can't answer yet. */
export function EmptyChat({
  scope,
  suggestions,
  onAsk,
  onChangeScope,
  noProvider,
  admin,
}: {
  scope: Scope;
  suggestions: string[];
  onAsk: (q: string) => void;
  onChangeScope: () => void;
  noProvider: boolean;
  admin: boolean;
}) {
  if (noProvider)
    return (
      <div className="flex max-w-[420px] flex-col gap-4 rounded-lg border border-border bg-surface p-4">
        <div className="flex flex-col gap-1.5">
          <h2 className="text-[17px] font-bold text-fg">Chat needs an LLM provider</h2>
          <p className="m-0 text-[13.5px] leading-normal text-fg-strong">
            No OpenAI-compatible provider is set up yet. Search still works, and questions here get the best matching passages. An admin can add one in Settings → LLM provider.
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button asChild={admin} variant="secondary" size="sm" disabled={!admin} disabledReason="Only admins can change Settings" icon={admin ? undefined : <Settings />}>
            {admin ? (
              <Link href="/settings#llm">
                <Settings /> Open LLM settings
              </Link>
            ) : (
              "Open LLM settings (admins)"
            )}
          </Button>
          <Button asChild variant="ghost" size="sm">
            <Link href="/search">Go to Search</Link>
          </Button>
        </div>
      </div>
    );
  const title = scope.namespaces?.length === 1 ? `Ask about ${scope.namespaces[0]}` : "Ask about your archive";
  return (
    <div className="flex max-w-[420px] flex-col gap-3">
      <div className="flex flex-col gap-1.5 rounded-lg border border-border bg-surface p-4">
        <h2 className="text-[17px] font-bold text-fg">{title}</h2>
        <p className="m-0 text-[13.5px] leading-normal text-fg-secondary">
          Answers come only from recordings you can read{scope.namespaces?.length ? ` in ${scopeWords(scope)}` : ""}, and cite the moment they’re based on.
        </p>
      </div>
      <ul className="m-0 flex list-none flex-col gap-2 p-0" aria-label="Suggested questions">
        {suggestions.map((s) => (
          <li key={s}>
            <button type="button" onClick={() => onAsk(s)} className="w-full rounded-md border border-border bg-background px-3 py-2.5 text-left text-[13.5px] font-medium leading-snug text-fg hover:border-blue-border hover:bg-blue-surface">
              {s}
            </button>
          </li>
        ))}
      </ul>
      <div>
        <Button variant="secondary" size="sm" onClick={onChangeScope}>
          Change scope
        </Button>
      </div>
    </div>
  );
}
