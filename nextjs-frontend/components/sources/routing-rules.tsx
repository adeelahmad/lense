"use client";

import { useQuery } from "@tanstack/react-query";
import { ArrowDown, ArrowUp, Plus, X } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { Sources } from "@/app/openapi-client";
import {
  blankRule,
  move,
  ROUTE_FIELDS,
  type RouteField,
  type RuleForm,
  ruleProblem,
  rulesToApi,
  SKIP,
} from "@/components/sources/route-model";
import { Button, IconButton } from "@/components/ui/button";
import { Input, Select } from "@/components/ui/field";
import { data, useApiClient } from "@/lib/api/browser";

function useDebounced<T>(value: T, ms: number): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return v;
}

/** An email watch's routing rules: the first rule that matches a new message sends it to a namespace, or skips it;
 * the rest go to the watch's own namespace. Below them, where the latest messages would go. */
export function RoutingRules({
  rules,
  onChange,
  namespaces,
  home,
  source,
  path,
  showProblems,
}: {
  rules: RuleForm[];
  onChange: (rules: RuleForm[]) => void;
  namespaces: string[];
  home: string;
  source: number;
  path: string;
  showProblems: boolean;
}) {
  const client = useApiClient();
  const update = (i: number, r: Partial<RuleForm>) => onChange(rules.map((x, j) => (j === i ? { ...x, ...r } : x)));
  const setCondition = (i: number, c: number, v: Partial<{ field: RouteField; patterns: string }>) =>
    update(i, { conditions: rules[i].conditions.map((x, k) => (k === c ? { ...x, ...v } : x)) });

  const ready = rules.every((r) => !ruleProblem(r));
  const body = useDebounced(
    useMemo(() => ({ source, path, namespace: home, routes: rulesToApi(rules) }), [source, path, home, rules]),
    600,
  );
  const preview = useQuery({
    queryKey: ["route-preview", body],
    queryFn: () => data(Sources.previewRoutes({ client, body })),
    enabled: ready && rules.length > 0 && Boolean(home),
    staleTime: 60_000,
    retry: false,
  });

  const targets = [...namespaces.map((n) => ({ value: n, label: n })), { value: SKIP, label: "Skip: don’t import" }];

  return (
    <div className="flex flex-col gap-2" role="group" aria-labelledby="watch-routes">
      <span className="text-[13px] font-bold text-fg-strong" id="watch-routes">
        Routing rules
      </span>
      <p className="text-[12.5px] text-fg-muted">
        Send some messages to other namespaces. The first rule that matches decides; messages no rule matches go to{" "}
        {home || "this watch’s namespace"}. Patterns are comma-separated and match anywhere, or use * for a pattern such
        as *@acme.com. Rules apply to mail that arrives after you save.
      </p>
      {rules.map((r, i) => {
        const problem = showProblems ? ruleProblem(r) : null;
        return (
          <div key={r.key} className="flex flex-col gap-2 rounded-md border border-border bg-background p-2.5">
            {r.conditions.map((c, k) => (
              <div key={k} className="flex items-center gap-2">
                <span className="w-9 shrink-0 text-[12.5px] text-fg-muted">{k ? "and" : "If"}</span>
                <Select
                  size="sm"
                  className="w-36"
                  aria-label="Look at"
                  value={c.field}
                  onChange={(e) => setCondition(i, k, { field: e.target.value as RouteField })}
                  options={ROUTE_FIELDS.map(({ value, label }) => ({ value, label }))}
                />
                <Input
                  className="h-8 min-w-0 flex-1"
                  mono
                  aria-label="Patterns"
                  placeholder={ROUTE_FIELDS.find((x) => x.value === c.field)?.placeholder}
                  value={c.patterns}
                  onChange={(e) => setCondition(i, k, { patterns: e.target.value })}
                />
                {r.conditions.length > 1 && (
                  <IconButton
                    label="Remove condition"
                    size={28}
                    onClick={() => update(i, { conditions: r.conditions.filter((_, j) => j !== k) })}
                  >
                    <X />
                  </IconButton>
                )}
              </div>
            ))}
            <div className="flex flex-wrap items-center gap-2">
              <span className="w-9 shrink-0 text-[12.5px] text-fg-muted">then</span>
              <Select
                size="sm"
                className="w-52"
                aria-label="Send to"
                value={r.target ?? "?"}
                onChange={(e) => update(i, { target: e.target.value })}
                options={[...(r.target === null ? [{ value: "?", label: "Choose a namespace…" }] : []), ...targets]}
              />
              <Button
                size="xs"
                variant="ghost"
                icon={<Plus />}
                onClick={() => update(i, { conditions: [...r.conditions, { field: "subject", patterns: "" }] })}
              >
                Condition
              </Button>
              <span className="flex-1" />
              <IconButton label="Move up" size={28} disabled={i === 0} onClick={() => onChange(move(rules, i, i - 1))}>
                <ArrowUp />
              </IconButton>
              <IconButton
                label="Move down"
                size={28}
                disabled={i === rules.length - 1}
                onClick={() => onChange(move(rules, i, i + 1))}
              >
                <ArrowDown />
              </IconButton>
              <IconButton label="Remove rule" size={28} onClick={() => onChange(rules.filter((_, j) => j !== i))}>
                <X />
              </IconButton>
            </div>
            {problem && (
              <p role="alert" className="text-[12.5px] text-red-dark">
                {problem}
              </p>
            )}
          </div>
        );
      })}
      <Button
        size="sm"
        className="self-start"
        icon={<Plus />}
        onClick={() => onChange([...rules, blankRule(namespaces.find((n) => n !== home) ?? null)])}
      >
        Add a rule
      </Button>
      {rules.length > 0 && (
        <div
          className="flex flex-col gap-1 rounded-md border border-blue-border bg-blue-surface p-2.5"
          aria-live="polite"
        >
          <span className="text-[12.5px] font-semibold text-fg-accent">
            {!ready
              ? "Finish the rules to see where the latest messages would go"
              : preview.isFetching
                ? "Checking the latest messages…"
                : preview.error
                  ? `Couldn’t check the messages: ${(preview.error as Error).message}`
                  : "Where the latest messages would go"}
          </span>
          {ready && !preview.error && (
            <ul className="flex max-h-48 flex-col gap-0.5 overflow-y-auto text-[12.5px]">
              {(preview.data ?? []).slice(0, 20).map((m) => (
                <li key={m.path} className="flex gap-2">
                  <span className="min-w-0 flex-1 truncate" title={m.from?.join(", ")}>
                    {m.title || "(no subject)"}
                  </span>
                  <span className={m.skipped ? "text-fg-muted" : "font-semibold text-fg-strong"}>
                    {m.skipped ? "skipped" : `→ ${m.namespace}`}
                  </span>
                  <span className="w-12 shrink-0 text-right text-fg-muted">{m.rule ? `rule ${m.rule}` : ""}</span>
                </li>
              ))}
              {preview.data?.length === 0 && <li className="text-fg-muted">No messages there yet.</li>}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
