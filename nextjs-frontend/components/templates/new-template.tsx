"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { Templates } from "@/app/openapi-client";
import { Button } from "@/components/ui/button";
import { Field, Input, Textarea } from "@/components/ui/field";
import { EmptyState } from "@/components/ui/states";
import { Segmented } from "@/components/ui/tabs";
import { useToast } from "@/components/ui/toast";
import { data, useApiClient } from "@/lib/api/browser";
import { useArchive } from "@/lib/hooks/session";

type Kind = "prompt" | "report" | "export";

/** Starting points, so a new template renders straight away. */
const STARTERS: Record<Kind, { body: string; schema?: Record<string, unknown> }> = {
  prompt: {
    body: 'Summarise "{{ recording.title }}" ({{ recording.duration }}).\n\nSpeakers: {% for s in speakers %}{{ s.name }}{% if not loop.last %}, {% endif %}{% endfor %}\n\nTranscript:\n{{ transcript }}\n\nReturn JSON matching the schema.\n',
    schema: { type: "object", required: ["tldr", "key_points"], properties: { tldr: { type: "string" }, key_points: { type: "array", items: { type: "string" }, minItems: 3, maxItems: 6 } } },
  },
  report: {
    body: '<!doctype html><html><head><meta charset="utf-8"><title>{{ recording.title }}</title></head><body>\n<h1>{{ recording.title }}</h1>\n<p>{{ recording.namespace }} · {{ recording.recorded_at }} · {{ recording.duration }}</p>\n<h2>Topics</h2><p>{{ keywords[:15] | join(", ") }}</p>\n</body></html>\n',
  },
  export: {
    body: "# {{ recording.title }}\n\n{% for s in segments %}**{{ s.speaker }}** ({{ s.time }}): {{ s.text }}\n\n{% endfor %}",
  },
};

export function NewTemplate() {
  const client = useApiClient();
  const qc = useQueryClient();
  const router = useRouter();
  const toast = useToast();
  const { admin } = useArchive();
  const [name, setName] = useState("");
  const [kind, setKind] = useState<Kind>("prompt");
  const [description, setDescription] = useState("");
  const create = useMutation({
    mutationFn: () =>
      data(Templates.createTemplate({ client, body: { name: name.trim(), kind, description: description.trim() || null, body: STARTERS[kind].body, schema: STARTERS[kind].schema ?? null } })),
    onSuccess: (r) => {
      void qc.invalidateQueries({ queryKey: ["templates"] });
      toast({ tone: "green", title: "Template created", body: name });
      router.push(`/templates/${r.id}`);
    },
    onError: (e: Error) => toast({ tone: "red", title: "Couldn’t create the template", body: e.message }),
  });
  if (!admin)
    return (
      <EmptyState
        title="Only admins can create templates"
        actions={
          <Button asChild variant="secondary">
            <Link href="/templates">All templates</Link>
          </Button>
        }
      >
        You can open and preview the existing ones.
      </EmptyState>
    );
  return (
    <div className="mx-auto flex max-w-[560px] flex-col gap-4 px-4 pb-10 pt-[18px]">
      <nav aria-label="Breadcrumb" className="text-[12px] font-medium text-fg-muted">
        <Link href="/templates" className="hover:text-fg hover:underline">
          Templates
        </Link>{" "}
        › New
      </nav>
      <h1 className="text-[24px] font-bold leading-tight tracking-[-.015em] text-fg">New template</h1>
      <Field label="Name">{({ id }) => <Input id={id} autoFocus value={name} maxLength={80} onChange={(e) => setName(e.target.value)} placeholder="Podcast summary" />}</Field>
      <div className="flex flex-col gap-1.5">
        <span className="text-[13px] font-bold text-fg-strong">Kind</span>
        <Segmented
          className="self-start"
          value={kind}
          onChange={(v) => setKind(v as Kind)}
          items={[
            { value: "prompt", label: "Prompt" },
            { value: "report", label: "Report page" },
            { value: "export", label: "Export file" },
          ]}
        />
        <span className="text-[12.5px] text-fg-muted">
          {kind === "prompt"
            ? "Sent to the model by an LLM step; its answer must match an output schema and is saved on the recording."
            : kind === "report"
              ? "An HTML page for a report step (scripts don’t run in it)."
              : "A text file (Markdown, CSV…) written by an export step, optionally copied to a source."}
        </span>
      </div>
      <Field label="Description" optional>
        {({ id }) => <Textarea id={id} rows={2} className="min-h-[60px]" value={description} onChange={(e) => setDescription(e.target.value)} />}
      </Field>
      <div className="flex justify-end gap-2">
        <Button asChild variant="ghost">
          <Link href="/templates">Cancel</Link>
        </Button>
        <Button variant="primary" disabled={!name.trim() || create.isPending} disabledReason="Give it a name" onClick={() => create.mutate()}>
          {create.isPending ? "Creating…" : "Create and edit"}
        </Button>
      </div>
    </div>
  );
}
