"use client";

import { FileText } from "lucide-react";
import Link from "next/link";

import type { TemplateSummary } from "@/app/openapi-client/types.gen";
import { CatalogHeader, useTemplateList } from "@/components/pipelines/catalog-header";
import { useTemplateUsage } from "@/components/templates/use-template-usage";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { EmptyState, SkeletonRows } from "@/components/ui/states";
import { Table, Td, Th, THead, Tr } from "@/components/ui/table";
import { relative } from "@/lib/format";

const KIND: Record<string, { tone: "intent" | "green" | "gate"; word: string; help: string }> = {
  prompt: { tone: "gate", word: "Prompt", help: "An LLM prompt with an output schema" },
  report: { tone: "intent", word: "Report", help: "An HTML page" },
  export: { tone: "green", word: "Export", help: "A file (Markdown, text, CSV…)" },
};

/** Templates (TP list): prompts for LLM steps, report pages and export files, versioned. */
export function TemplatesPage() {
  const templates = useTemplateList();
  const { usage } = useTemplateUsage();
  const rows = (templates.data ?? []) as TemplateSummary[];
  return (
    <div className="flex flex-col gap-4 px-4 pb-10 pt-[18px] md:px-6">
      <CatalogHeader tab="templates" />
      {templates.isLoading ? (
        <SkeletonRows rows={4} />
      ) : templates.error ? (
        <EmptyState tone="error" icon={<FileText />} title="Couldn’t load templates" actions={<Button onClick={() => templates.refetch()}>Try again</Button>}>
          {(templates.error as Error).message}
        </EmptyState>
      ) : !rows.length ? (
        <EmptyState icon={<FileText />} title="No templates yet">
          A prompt template turns a recording into structured notes with your own questions; report and export templates shape pages and files.
        </EmptyState>
      ) : (
        <div className="overflow-hidden rounded-md border border-border">
          <Table aria-label="Templates">
            <THead className="border-t-0">
              <tr>
                <Th>Name</Th>
                <Th>Kind</Th>
                <Th>Version</Th>
                <Th>Used by</Th>
                <Th>Updated</Th>
              </tr>
            </THead>
            <tbody>
              {rows.map((t) => {
                const k = KIND[t.kind] ?? KIND.export;
                const used = usage.get(t.id) ?? [];
                return (
                  <Tr key={t.id} className="h-[54px]">
                    <Td>
                      <div className="flex min-w-0 flex-col gap-0.5">
                        <Link href={`/templates/${t.id}`} className="font-semibold text-fg hover:text-fg-accent hover:underline">
                          {t.name}
                        </Link>
                        {t.description && <span className="line-clamp-1 text-[12px] text-fg-muted">{t.description}</span>}
                      </div>
                    </Td>
                    <Td title={k.help}>
                      <Badge tone={k.tone}>{k.word}</Badge>
                    </Td>
                    <Td>
                      <code className="font-mono text-[12px] font-medium text-fg">v{t.current}</code>
                      <span className="ml-1.5 text-[12px] text-fg-muted">{t.versions === 1 ? "1 version" : `${t.versions} versions`}</span>
                    </Td>
                    <Td className="text-[12.5px] text-fg-secondary">{used.length ? used.join(" · ") : "—"}</Td>
                    <Td className="tabular whitespace-nowrap text-fg-muted">{relative(t.updated_at)}</Td>
                  </Tr>
                );
              })}
            </tbody>
          </Table>
        </div>
      )}
    </div>
  );
}
