"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Download, Share2, Upload } from "lucide-react";
import { useRef, useState } from "react";

import { Rdf } from "@/app/openapi-client";
import type { RdfImportResult } from "@/app/openapi-client/types.gen";
import { Banner } from "@/components/ui/banner";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { Textarea } from "@/components/ui/field";
import { Menu, MenuContent, MenuItem, MenuTrigger } from "@/components/ui/menu";
import { useToast } from "@/components/ui/toast";
import { data, useApiClient } from "@/lib/api/browser";

/** The RDF formats the server writes (docs/rdf.md), with the file suffix each is saved with. */
export const RDF_FORMATS = [
  { format: "turtle", label: "Turtle", ext: "ttl", type: "text/turtle" },
  { format: "json-ld", label: "JSON-LD", ext: "jsonld", type: "application/ld+json" },
  { format: "xml", label: "RDF/XML", ext: "rdf", type: "application/rdf+xml" },
  { format: "nt", label: "N-Triples", ext: "nt", type: "application/n-triples" },
] as const;
export type RdfFormat = (typeof RDF_FORMATS)[number]["format"];

/** What a recording or a namespace is called in RDF: its linked-data URI on this site. */
export function linkedDataUri(origin: string, target: { recording: number } | { namespace: string }): string {
  return "recording" in target
    ? `${origin}/id/recording/${target.recording}`
    : `${origin}/id/namespace/${encodeURIComponent(target.namespace)}`;
}

/** The file name a download is saved as: "pods.ttl", "recording-12.jsonld". */
export function rdfFileName(target: { recording: number } | { namespace: string }, format: RdfFormat): string {
  const ext = RDF_FORMATS.find((f) => f.format === format)?.ext ?? "ttl";
  const stem = "recording" in target ? `recording-${target.recording}` : target.namespace.replace(/[^\w.-]+/g, "_");
  return `${stem}.${ext}`;
}

function save(text: string, name: string, type: string) {
  const url = URL.createObjectURL(new Blob([text], { type }));
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/** "RDF" menu: save a recording's (or a whole namespace's) description as Turtle, JSON-LD, RDF/XML or N-Triples, or
 * copy its linked-data URI. */
export function RdfMenu({ target }: { target: { recording: number } | { namespace: string } }) {
  const client = useApiClient();
  const toast = useToast();
  const download = async (format: RdfFormat) => {
    const meta = RDF_FORMATS.find((f) => f.format === format)!;
    try {
      const text = (await data(
        "recording" in target
          ? Rdf.getRecordingRdf({ client, path: { rid: target.recording }, query: { format }, parseAs: "text" })
          : Rdf.getNamespaceRdf({ client, path: { name: target.namespace }, query: { format }, parseAs: "text" }),
      )) as unknown as string;
      save(text, rdfFileName(target, format), meta.type);
    } catch (e) {
      toast({ title: "Couldn’t export RDF", body: (e as Error).message, tone: "red" });
    }
  };
  const copy = async () => {
    await navigator.clipboard.writeText(linkedDataUri(window.location.origin, target));
    toast({
      title: "Linked-data URI copied",
      body: "RDF clients get its description there; browsers get the page.",
      tone: "green",
    });
  };
  return (
    <Menu>
      <MenuTrigger asChild>
        <Button size="sm" variant="ghost" icon={<Share2 />}>
          RDF
        </Button>
      </MenuTrigger>
      <MenuContent>
        {RDF_FORMATS.map((f) => (
          <MenuItem key={f.format} icon={<Download />} onSelect={() => void download(f.format)}>
            Save as {f.label}
          </MenuItem>
        ))}
        <MenuItem onSelect={() => void copy()}>Copy linked-data URI</MenuItem>
      </MenuContent>
    </Menu>
  );
}

/** A summary of an import in words: "2 recordings would change, 1 description matched nothing". */
export function importSummary(r: RdfImportResult): string {
  const n = (x: number, what: string) => `${x} ${what}${x === 1 ? "" : "s"}`;
  const changed = `${n(r.changed, "recording")} ${r.dry_run ? "would change" : "changed"}`;
  const same = r.matched - r.changed;
  return [
    changed,
    same ? `${n(same, "description")} already said` : "",
    r.unmatched.length ? `${n(r.unmatched.length, "description")} matched nothing` : "",
  ]
    .filter(Boolean)
    .join(", ");
}

/**
 * Read Dublin Core RDF (Turtle, N-Triples or JSON-LD) into a namespace's recordings: pick a file or paste it, see what
 * would change, then import. Every change goes into the recordings' metadata history.
 */
export function RdfImportDialog({ ns, open, onClose }: { ns: string; open: boolean; onClose: () => void }) {
  const client = useApiClient();
  const qc = useQueryClient();
  const toast = useToast();
  const file = useRef<HTMLInputElement>(null);
  const [text, setText] = useState("");
  const [preview, setPreview] = useState<RdfImportResult | null>(null);
  const run = useMutation({
    mutationFn: async (dryRun: boolean) =>
      (await data(
        Rdf.importNamespaceRdf({ client, path: { name: ns }, body: { data: text, dry_run: dryRun } }),
      )) as unknown as RdfImportResult,
    onSuccess: (r) => {
      if (r.dry_run) {
        setPreview(r);
        return;
      }
      void qc.invalidateQueries();
      toast({ title: "RDF imported", body: importSummary(r), tone: "green" });
      close();
    },
  });
  const close = () => {
    setText("");
    setPreview(null);
    run.reset();
    onClose();
  };
  return (
    <Dialog
      open={open}
      onOpenChange={(o) => !o && close()}
      wide
      title={`Import RDF into ${ns}`}
      description="Dublin Core descriptions in Turtle, N-Triples or JSON-LD. Each is matched to a recording by its Lens URI or an identifier it already has; every change is kept in its history."
      actions={
        <>
          <Button variant="ghost" onClick={close}>
            Cancel
          </Button>
          {preview && preview.changed > 0 ? (
            <Button variant="primary" disabled={run.isPending} onClick={() => run.mutate(false)}>
              {run.isPending ? "Importing…" : `Import ${preview.changed} change${preview.changed === 1 ? "" : "s"}`}
            </Button>
          ) : (
            <Button variant="primary" disabled={!text.trim() || run.isPending} onClick={() => run.mutate(true)}>
              {run.isPending ? "Reading…" : "Preview"}
            </Button>
          )}
        </>
      }
    >
      <div className="flex flex-col gap-3">
        <div className="flex items-center gap-2">
          <Button size="sm" variant="ghost" icon={<Upload />} onClick={() => file.current?.click()}>
            Choose a file
          </Button>
          <span className="text-[12px] text-fg-muted">or paste it below</span>
          <input
            ref={file}
            type="file"
            hidden
            accept=".ttl,.nt,.jsonld,.json,text/turtle,application/ld+json,application/n-triples"
            onChange={async (e) => {
              const f = e.target.files?.[0];
              if (f) {
                setText(await f.text());
                setPreview(null);
              }
              e.target.value = "";
            }}
          />
        </div>
        <Textarea
          aria-label="RDF"
          mono
          rows={10}
          placeholder={
            '@prefix dcterms: <http://purl.org/dc/terms/> .\n<https://…/id/recording/12> dcterms:spatial "Berlin" .'
          }
          value={text}
          onChange={(e) => {
            setText(e.target.value);
            setPreview(null);
          }}
        />
        {run.error && <Banner tone="error">{run.error.message}</Banner>}
        {preview && (
          <div className="flex flex-col gap-1.5 text-[13px]" aria-live="polite">
            <b>{importSummary(preview)}</b>
            <ul className="flex flex-col gap-1">
              {preview.items
                .filter((i) => i.fields.length || i.notes.length)
                .map((i) => (
                  <li key={i.subject}>
                    Recording {i.recording}: {i.fields.length ? i.fields.join(", ") : "no change"}
                    {i.notes.map((n) => (
                      <span key={n} className="block text-[12px] text-fg-muted">
                        {n}
                      </span>
                    ))}
                  </li>
                ))}
              {preview.unmatched.slice(0, 10).map((u) => (
                <li key={u.subject} className="text-fg-muted">
                  No recording for {u.title ? `“${u.title}”` : u.subject}
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </Dialog>
  );
}

type SparqlTerm = { type: string; value: string; "xml:lang"?: string };
export type SparqlResults = {
  head: { vars?: string[] };
  boolean?: boolean;
  results?: { bindings: Record<string, SparqlTerm>[] };
};

export const SPARQL_EXAMPLE = `# The 20 entities mentioned in the most recordings
SELECT ?entity ?name (COUNT(?r) AS ?recordings) WHERE {
  ?r dcterms:references ?entity .
  ?entity skos:prefLabel ?name .
}
GROUP BY ?entity ?name
ORDER BY DESC(?recordings)
LIMIT 20`;

/** A result cell in words: a value, with its language when it has one. */
export function sparqlCell(t: SparqlTerm | undefined): string {
  if (!t) return "";
  return t["xml:lang"] ? `${t.value} @${t["xml:lang"]}` : t.value;
}

/** A read-only SPARQL query over a namespace's linked data, with its results as a table (or Turtle). */
export function SparqlDialog({ ns, open, onClose }: { ns: string; open: boolean; onClose: () => void }) {
  const client = useApiClient();
  const [query, setQuery] = useState(SPARQL_EXAMPLE);
  const run = useMutation({
    mutationFn: async () => {
      const out = await data(
        Rdf.postNamespaceSparql({
          client,
          path: { name: ns },
          body: { query },
          query: { format: "turtle" },
          parseAs: "text",
        }),
      );
      const text = out as unknown as string;
      try {
        return { results: JSON.parse(text) as SparqlResults };
      } catch {
        return { turtle: text };
      }
    },
  });
  const res = run.data && "results" in run.data ? run.data.results : null;
  const vars = res?.head.vars ?? [];
  return (
    <Dialog
      open={open}
      onOpenChange={(o) => !o && onClose()}
      wide
      title={`Query ${ns} with SPARQL`}
      description="Read-only: SELECT, ASK, CONSTRUCT or DESCRIBE over this namespace's recordings (Dublin Core), collections, entities and speakers. dcterms, skos, foaf, owl, rdfs, xsd and lens are known prefixes."
      actions={
        <>
          <Button variant="ghost" onClick={onClose}>
            Close
          </Button>
          <Button variant="primary" disabled={!query.trim() || run.isPending} onClick={() => run.mutate()}>
            {run.isPending ? "Running…" : "Run"}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-3">
        <Textarea aria-label="SPARQL query" mono rows={8} value={query} onChange={(e) => setQuery(e.target.value)} />
        {run.error && <Banner tone="error">{run.error.message}</Banner>}
        {res?.boolean !== undefined && <b aria-live="polite">{res.boolean ? "Yes" : "No"}</b>}
        {res?.results && (
          <div className="max-h-[40vh] overflow-auto rounded-md border border-border" aria-live="polite">
            <table className="w-full text-left text-[12.5px]">
              <thead className="sticky top-0 bg-surface">
                <tr>
                  {vars.map((v) => (
                    <th key={v} className="px-2 py-1.5 font-mono font-semibold">
                      ?{v}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {res.results.bindings.map((row, i) => (
                  <tr key={i} className="border-t border-border">
                    {vars.map((v) => (
                      <td key={v} className="max-w-[320px] truncate px-2 py-1">
                        {sparqlCell(row[v])}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
            {!res.results.bindings.length && <p className="p-3 text-fg-muted">No results.</p>}
          </div>
        )}
        {run.data && "turtle" in run.data && (
          <pre className="max-h-[40vh] overflow-auto rounded-md border border-border p-2 text-[12px]">
            {run.data.turtle}
          </pre>
        )}
      </div>
    </Dialog>
  );
}
