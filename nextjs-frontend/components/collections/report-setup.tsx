"use client";

import { useQuery } from "@tanstack/react-query";
import { useRouter, useSearchParams } from "next/navigation";
import { useState } from "react";

import { Entities, Templates } from "@/app/openapi-client";
import { useCollections } from "@/components/collections/collections-page";
import { Banner } from "@/components/ui/banner";
import { Button } from "@/components/ui/button";
import { Field, Select, Textarea } from "@/components/ui/field";
import { EmptyState, Skeleton } from "@/components/ui/states";
import { data, useApiClient } from "@/lib/api/browser";
import { count } from "@/lib/format";
import { needRole, useArchive } from "@/lib/hooks/session";

function Stage({ n, children }: { n: number; children: React.ReactNode }) {
  return (
    <div className="flex items-center gap-3">
      <span
        aria-hidden
        className="grid size-[26px] shrink-0 place-items-center rounded-full bg-blue text-[12px] font-bold text-white"
      >
        {n}
      </span>
      <div className="min-w-0 flex-1">{children}</div>
    </div>
  );
}

/**
 * CR1 setup: a collection report reads each recording with a prompt template, then combines the results into one
 * page that cites every recording. It runs as a batch, so it gets the same double-check first.
 */
export function ReportSetup() {
  const params = useSearchParams();
  const router = useRouter();
  const client = useApiClient();
  const { namespaces, can } = useArchive();
  const cols = useCollections();
  const templates = useQuery({
    queryKey: ["templates"],
    queryFn: () => data(Templates.listTemplates({ client })),
    staleTime: 60_000,
  });
  const ents = useQuery({
    queryKey: ["entities", "top"],
    queryFn: () => data(Entities.listEntities({ client, query: { limit: 30 } })),
    staleTime: 60_000,
  });
  const [set, setSet] = useState(
    params.get("collection")
      ? `c:${params.get("collection")}`
      : params.get("ns")
        ? `n:${params.get("ns")}`
        : params.get("entity")
          ? `e:${params.get("entity")}`
          : "",
  );
  const [tpl, setTpl] = useState("");
  const [instructions, setInstructions] = useState("");
  const [output, setOutput] = useState("page");
  const prompts = (templates.data ?? []).filter((t) => t.kind === "prompt");

  if (!can("editor"))
    return (
      <div className="px-4 py-6 md:px-6">
        <EmptyState title="Collection reports need editor access">
          {needRole("editor")}. They run a template on every recording in the set.
        </EmptyState>
      </div>
    );

  const go = () => {
    const p = new URLSearchParams();
    const [kind, value] = [set.slice(0, 1), set.slice(2)];
    if (kind === "c") p.set("collection", value);
    if (kind === "n") p.set("ns", value);
    if (kind === "e") {
      p.set("entity", value);
      const e = ents.data?.items.find((x) => String(x.id) === value);
      if (e) p.set("label", String(e.name));
    }
    p.set("template", tpl);
    p.set("combine", instructions.trim());
    router.push(`/batches/new?${p}`);
  };

  const options = [
    { value: "", label: "Choose a set…" },
    ...(cols.data ?? []).map((c) => ({
      value: `c:${c.id}`,
      label: `Saved collection · ${c.name} (${count(c.count)})`,
    })),
    ...namespaces.map((n) => ({
      value: `n:${n.name}`,
      label: `Namespace · ${n.name} (${count((n.recordings as number) ?? 0)})`,
    })),
    ...(ents.data?.items ?? []).map((e) => ({
      value: `e:${e.id}`,
      label: `Entity · ${String(e.name)} (${count(Number(e.recordings))})`,
    })),
  ];
  return (
    <div className="flex justify-center px-4 py-6 md:px-6">
      <div className="flex w-full max-w-[560px] flex-col gap-4 rounded-lg border border-border p-5">
        <h1 className="text-[17px] font-bold text-fg">New collection report</h1>
        {(cols.isLoading || templates.isLoading) && <Skeleton className="h-10 w-full" />}
        {templates.isError && <Banner tone="error">{templates.error.message}</Banner>}
        <Field label="Set">
          {({ id }) => <Select id={id} value={set} onChange={(e) => setSet(e.target.value)} options={options} />}
        </Field>
        <fieldset className="m-0 flex flex-col gap-2.5 border-0 p-0">
          <legend className="mb-2 text-[13px] font-bold text-fg-strong">Stages</legend>
          <Stage n={1}>
            <Select
              aria-label="Extract per recording"
              value={tpl}
              onChange={(e) => setTpl(e.target.value)}
              options={[
                {
                  value: "",
                  label: prompts.length
                    ? "Extract per recording · choose a prompt template"
                    : "No prompt templates yet",
                },
                ...prompts.map((t) => ({
                  value: String(t.id),
                  label: `Extract per recording · ${t.name}`,
                })),
              ]}
            />
          </Stage>
          <Stage n={2}>
            <Textarea
              aria-label="Combine: what the report should cover"
              rows={2}
              value={instructions}
              onChange={(e) => setInstructions(e.target.value)}
              placeholder="Combine · the main themes, how many recordings raise each, and what changed over time"
            />
          </Stage>
        </fieldset>
        <Field label="Output" hint="The report is a page you can export as Markdown. PDF isn’t available yet.">
          {({ id, describedBy }) => (
            <Select
              id={id}
              aria-describedby={describedBy}
              value={output}
              onChange={(e) => setOutput(e.target.value)}
              options={[
                { value: "page", label: "Report page" },
                {
                  value: "pdf",
                  label: "Report page + PDF (not available yet)",
                  disabled: true,
                },
              ]}
            />
          )}
        </Field>
        <div className="flex justify-end">
          <Button
            variant="primary"
            disabled={!set || !tpl}
            disabledReason={!set ? "Choose a set" : "Choose a template for the first stage"}
            onClick={go}
          >
            Double-check
          </Button>
        </div>
      </div>
    </div>
  );
}
