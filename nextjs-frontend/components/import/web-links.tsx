"use client";

import { useMutation } from "@tanstack/react-query";
import { useId, useRef, useState, type FormEvent } from "react";

import { Imports } from "@/app/openapi-client";
import type { LinkImportResult } from "@/app/openapi-client/types.gen";
import { fileToBase64 } from "@/components/import/files";
import { Button } from "@/components/ui/button";
import { Checkbox, Field, Textarea } from "@/components/ui/field";
import { useToast } from "@/components/ui/toast";
import { data, useApiClient } from "@/lib/api/browser";

const SAID = { queued: "Capturing", already: "Already here", skipped: "Skipped" } as const;

/**
 * Many pages at once: pasted addresses, or a browser's bookmark export (HTML) or Chrome's Bookmarks file. Each link
 * is captured like a single page; bookmark folders can become tags.
 */
export function WebLinks({
  namespace,
  blockReason,
  pipeline,
  collection,
}: {
  namespace: string | null;
  blockReason: string | null;
  pipeline: number | null;
  collection: number | null;
}) {
  const client = useApiClient();
  const toast = useToast();
  const id = useId();
  const picker = useRef<HTMLInputElement>(null);
  const [text, setText] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [folders, setFolders] = useState(true);
  const [results, setResults] = useState<LinkImportResult[]>([]);
  const send = useMutation({
    mutationFn: async () =>
      data(
        Imports.importWebLinks({
          client,
          body: {
            namespace: namespace ?? "",
            ...(file ? { filename: file.name, data: await fileToBase64(file) } : { text }),
            folders_as_tags: folders,
            pipeline,
            collection,
          },
        }),
      ),
    onSuccess: (r) => {
      setResults(r.results);
      const queued = r.results.filter((x) => x.status === "queued").length;
      toast({
        title: `Capturing ${queued} ${queued === 1 ? "page" : "pages"}`,
        body: "Each is kept as a PDF and read like any document.",
        tone: "green",
      });
      setText("");
      setFile(null);
      if (picker.current) picker.current.value = "";
    },
  });
  const empty = !file && !text.trim();
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (!empty && !blockReason) send.mutate();
  };
  return (
    <form onSubmit={submit} className="flex flex-col gap-3 border-t border-border pt-4" noValidate>
      <h2 className="text-[14px] font-bold text-fg-strong">Many pages at once</h2>
      <Field
        label="Links"
        optional
        hint="One address per line, or choose a bookmarks file below"
        error={send.error ? (send.error as Error).message : undefined}
      >
        {({ id: fid, describedBy, invalid }) => (
          <Textarea
            id={fid}
            aria-describedby={describedBy}
            invalid={invalid}
            rows={4}
            disabled={Boolean(file)}
            placeholder={"https://example.org/news\nhttps://example.org/tides"}
            value={text}
            onChange={(e) => {
              setText(e.target.value);
              send.reset();
            }}
          />
        )}
      </Field>
      <div className="flex flex-wrap items-center gap-3">
        <input
          ref={picker}
          id={`${id}-file`}
          type="file"
          accept=".html,.htm,.json,.txt,text/html,application/json,text/plain"
          className="text-[13px]"
          aria-label="A bookmarks file or a list of links"
          onChange={(e) => {
            setFile(e.target.files?.[0] ?? null);
            send.reset();
          }}
        />
      </div>
      <Checkbox checked={folders} onCheckedChange={setFolders} label="Tag each page with its bookmark folders" />
      <div>
        <Button
          type="submit"
          variant="secondary"
          disabled={Boolean(blockReason) || empty || send.isPending}
          disabledReason={blockReason ?? (empty ? "Paste links or choose a file" : undefined)}
        >
          {send.isPending ? "Adding…" : "Capture them all"}
        </Button>
      </div>
      {results.length > 0 && (
        <ul className="flex flex-col" aria-label="Links">
          {results.map((r) => (
            <li key={r.url} className="flex min-w-0 items-center gap-2 border-t border-border py-1.5 text-[12.5px]">
              <span
                className="min-w-0 flex-1 truncate font-mono text-[12px] text-fg-secondary"
                title={r.detail ?? r.url}
              >
                {r.url}
              </span>
              <span className={r.status === "skipped" ? "shrink-0 text-red-dark" : "shrink-0 text-fg-secondary"}>
                {SAID[r.status]}
              </span>
            </li>
          ))}
        </ul>
      )}
    </form>
  );
}
