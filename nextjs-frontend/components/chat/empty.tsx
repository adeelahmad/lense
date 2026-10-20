"use client";

import { Settings, Upload } from "lucide-react";
import Link from "next/link";

import { scopeWords, type Scope } from "@/components/chat/scope";
import { Button } from "@/components/ui/button";

/** Whether no asset you can read is in the scope (false while the list loads): chat has nothing to answer from. */
export function nothingInScope(rows: { namespace?: string | null }[] | undefined, scope: Scope): boolean {
  if (!rows) return false;
  return !rows.some(
    (r) => !scope.namespaces?.length || (r.namespace != null && scope.namespaces.includes(r.namespace)),
  );
}

/** CH2: before the first question — what chat answers from, suggested questions, or why it can't answer yet. */
export function EmptyChat({
  scope,
  suggestions,
  onAsk,
  onChangeScope,
  noProvider,
  admin,
  nothing = false,
  canImport = false,
}: {
  scope: Scope;
  suggestions: string[];
  onAsk: (q: string) => void;
  onChangeScope: () => void;
  noProvider: boolean;
  admin: boolean;
  /** No asset in scope yet: chat has nothing to answer from, so the next step is importing. */
  nothing?: boolean;
  /** You may import into at least one namespace (an editor or an admin). */
  canImport?: boolean;
}) {
  if (noProvider)
    return (
      <div className="flex max-w-[420px] flex-col gap-4 rounded-lg border border-border bg-surface p-4">
        <div className="flex flex-col gap-1.5">
          <h2 className="text-[17px] font-bold text-fg">Chat needs an LLM provider</h2>
          <p className="m-0 text-[13.5px] leading-normal text-fg-strong">
            No OpenAI-compatible provider is set up yet. Search still works, and questions here get the best matching
            passages. An admin can add one in Settings → LLM provider.
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button
            asChild={admin}
            variant="secondary"
            size="sm"
            disabled={!admin}
            disabledReason="Only admins can change Settings"
            icon={admin ? undefined : <Settings />}
          >
            {admin ? (
              <Link href="/settings/llm">
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
  if (nothing) {
    const scoped = Boolean(scope.namespaces?.length);
    return (
      <div className="flex max-w-[420px] flex-col gap-4 rounded-lg border border-border bg-surface p-4">
        <div className="flex flex-col gap-1.5">
          <h2 className="text-[17px] font-bold text-fg">
            {scoped ? `Nothing in ${scopeWords(scope)} yet` : "Nothing to answer from yet"}
          </h2>
          <p className="m-0 text-[13.5px] leading-normal text-fg-strong">
            Chat answers only from assets you can read, and cites them. Import a recording, a document or a web page,
            then ask about it here.
            {canImport ? "" : " An editor of a namespace or an admin can import; ask one to add you."}
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          {canImport && (
            <Button asChild variant="primary" size="sm">
              <Link href="/import">
                <Upload /> Import assets
              </Link>
            </Button>
          )}
          {scoped && (
            <Button variant="secondary" size="sm" onClick={onChangeScope}>
              Change scope
            </Button>
          )}
        </div>
      </div>
    );
  }
  const title = scope.namespaces?.length === 1 ? `Ask about ${scope.namespaces[0]}` : "Ask about your archive";
  return (
    <div className="flex max-w-[420px] flex-col gap-3">
      <div className="flex flex-col gap-1.5 rounded-lg border border-border bg-surface p-4">
        <h2 className="text-[17px] font-bold text-fg">{title}</h2>
        <p className="m-0 text-[13.5px] leading-normal text-fg-secondary">
          Answers come only from assets you can read
          {scope.namespaces?.length ? ` in ${scopeWords(scope)}` : ""}, and cite the moment they’re based on.
        </p>
      </div>
      <ul className="m-0 flex list-none flex-col gap-2 p-0" aria-label="Suggested questions">
        {suggestions.map((s) => (
          <li key={s}>
            <button
              type="button"
              onClick={() => onAsk(s)}
              className="w-full rounded-md border border-border bg-background px-3 py-2.5 text-left text-[13.5px] font-medium leading-snug text-fg hover:border-blue-border hover:bg-blue-surface"
            >
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
