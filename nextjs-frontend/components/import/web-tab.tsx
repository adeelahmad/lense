"use client";

import { useMutation } from "@tanstack/react-query";
import { Globe } from "lucide-react";
import Link from "next/link";
import { useId, useState, type FormEvent, type ReactNode } from "react";

import { Imports } from "@/app/openapi-client";
import { webAddressProblem } from "@/components/import/files";
import { Button } from "@/components/ui/button";
import { Field, Input } from "@/components/ui/field";
import { useToast } from "@/components/ui/toast";
import { data, useApiClient } from "@/lib/api/browser";

type Captured = { url: string; id: number };

/**
 * Import (web page): give an address; the server keeps the page as it is now, as a PDF (a PDF link as it is, other
 * pages printed by its browser), and reads it like any document. Public addresses only.
 */
export function WebTab({
  namespace,
  namespaceControl,
  pipelineControl,
  blockReason,
  pipeline,
  collection,
}: {
  namespace: string | null;
  namespaceControl: ReactNode;
  pipelineControl: ReactNode;
  /** Why capturing is blocked right now (no namespace, no rights), or null. */
  blockReason: string | null;
  pipeline: number | null;
  collection: number | null;
}) {
  const client = useApiClient();
  const toast = useToast();
  const id = useId();
  const [url, setUrl] = useState("");
  const [title, setTitle] = useState("");
  const [shown, setShown] = useState(false);
  const [captured, setCaptured] = useState<Captured[]>([]);
  const problem = webAddressProblem(url);
  const capture = useMutation({
    mutationFn: () =>
      data(
        Imports.importWebPage({
          client,
          body: { url: url.trim(), namespace: namespace ?? "", title: title.trim() || null, pipeline, collection },
        }),
      ),
    onSuccess: (r) => {
      setCaptured((c) => [{ url: url.trim(), id: r.id }, ...c].slice(0, 10));
      toast({ title: "Capturing the page", body: "It’s kept as a PDF and read like any document.", tone: "green" });
      setUrl("");
      setTitle("");
      setShown(false);
    },
  });
  const submit = (e: FormEvent) => {
    e.preventDefault();
    setShown(true);
    if (!problem && !blockReason) capture.mutate();
  };
  return (
    <div className="grid min-h-0 flex-1 gap-0 border-t border-border md:grid-cols-[minmax(0,1fr)_340px]">
      <form onSubmit={submit} className="flex flex-col gap-4 px-4 py-5 md:px-6" noValidate>
        <div className="flex items-start gap-3 rounded-md border border-border bg-surface p-3.5">
          <Globe aria-hidden className="mt-0.5 size-4 shrink-0 text-fg-secondary" />
          <p className="text-[13px] leading-[1.5] text-fg-secondary">
            The page is kept as it is now, as a PDF: a link to a PDF as it is, any other page as the server’s browser
            shows it. Its text is read page by page, like any document. Only public pages can be captured.
          </p>
        </div>
        <Field
          label="Page address"
          error={shown && problem ? problem : capture.error ? (capture.error as Error).message : undefined}
        >
          {({ id: fid, describedBy, invalid }) => (
            <Input
              id={fid}
              aria-describedby={describedBy}
              invalid={invalid}
              type="url"
              inputMode="url"
              autoComplete="off"
              placeholder="https://example.org/news/harbour-reopens"
              value={url}
              onChange={(e) => {
                setUrl(e.target.value);
                capture.reset();
              }}
            />
          )}
        </Field>
        <Field label="Title" optional hint="Default: the page’s own title">
          {({ id: fid, describedBy }) => (
            <Input
              id={fid}
              aria-describedby={describedBy}
              maxLength={200}
              value={title}
              onChange={(e) => setTitle(e.target.value)}
            />
          )}
        </Field>
        <div>
          <Button
            type="submit"
            variant="primary"
            disabled={Boolean(blockReason) || capture.isPending}
            disabledReason={blockReason ?? undefined}
          >
            {capture.isPending ? "Capturing…" : "Capture the page"}
          </Button>
        </div>
        {captured.length > 0 && (
          <section aria-labelledby={`${id}-done`} className="flex flex-col gap-1.5">
            <h2 id={`${id}-done`} className="text-[13px] font-bold text-fg-strong">
              Captured here
            </h2>
            <ul className="flex flex-col">
              {captured.map((c) => (
                <li key={c.id} className="flex min-w-0 items-center gap-2 border-t border-border py-2 text-[13px]">
                  <span className="min-w-0 flex-1 truncate font-mono text-[12px] text-fg-secondary">{c.url}</span>
                  <Link href={`/resources/${c.id}`} className="shrink-0 font-semibold text-fg-accent hover:underline">
                    Open
                  </Link>
                </li>
              ))}
            </ul>
          </section>
        )}
      </form>
      <aside className="flex flex-col gap-4 border-t border-border bg-surface px-4 py-5 md:border-l md:border-t-0 md:px-5">
        {namespaceControl}
        {pipelineControl}
      </aside>
    </div>
  );
}
