/** Plain-words labels for the recording header and details: exports, where a transcript came from, failures. */

export const EXPORTS: [string, string][] = [
  ["txt", "Plain text"],
  ["md", "Markdown"],
  ["srt", "Subtitles (SubRip)"],
  ["vtt", "Captions (WebVTT)"],
  ["json", "JSON"],
];

const IMPORT_FORMATS: Record<string, string> = {
  vtt: "WebVTT",
  srt: "SubRip",
  txt: "text",
  text: "text",
  md: "Markdown",
  markdown: "Markdown",
  json: "JSON",
  jsonl: "JSON lines",
  docx: "Word",
  doc: "Word",
  pdf: "PDF",
  paste: "pasted text",
  iiif: "IIIF",
};

/** "from WebVTT" for imported transcripts (engine "import:vtt"), or the transcription engine. */
export function transcriptOrigin(engine: string | null | undefined): string | null {
  if (!engine) return null;
  if (engine.startsWith("import:")) {
    const f = engine.slice(7);
    return `from ${IMPORT_FORMATS[f] ?? f.toUpperCase()}`;
  }
  return engine;
}

/**
 * Where the recording came from, for the meta line: uploads, a storage source, or a file on the server. `file` is true
 * when the text is a file name or path (set in monospace); "Pasted text" and "Uploaded · …" are words.
 */
export function sourceLabel(rec: { path?: string | null; remote?: { source?: number; path?: string } | null; source?: string | null }): { text: string; title: string; remote: boolean; file: boolean } | null {
  const path = rec.path ?? "";
  if (rec.remote?.path) return { text: rec.remote.path, title: `Storage source ${rec.remote.source ?? ""} · ${rec.remote.path}`, remote: true, file: true };
  if (!path) return null;
  if (path.startsWith("upload:")) return { text: `Uploaded · ${path.slice(7)}`, title: "Uploaded in the app", remote: false, file: false };
  if (path.startsWith("paste:")) return { text: "Pasted text", title: "Pasted into the importer", remote: false, file: false };
  const base = path.split(/[\\/]/).pop() || path;
  return { text: base, title: path, remote: false, file: true };
}

/**
 * What still works after a step failed, in the banner's words. `done` lists the steps this run finished; a run that
 * didn't start from the transcript (a single step, a template) leaves everything else as it was.
 */
export function failureImpact(step: string | null, done: string[], partial = false, label?: string): string {
  const missing: Record<string, string> = { summarize: "the summary", report: "the report", analyze: "chapters, entities and stats", llm: label ? `the ${label} output` : "the template output" };
  const what = step ? missing[step] : undefined;
  if (partial && !done.length) return what ? `Everything else is as it was; only ${what} is missing.` : "Everything else is as it was.";
  const ready = done.length ? `${listJoin(done)} ${done.length === 1 ? "is" : "are"} ready` : "Nothing after the import is ready yet";
  return what ? `${ready}; only ${what} is missing.` : `${ready}.`;
}

function listJoin(xs: string[]): string {
  return xs.length <= 1 ? (xs[0] ?? "") : `${xs.slice(0, -1).join(", ")} and ${xs[xs.length - 1]}`;
}

