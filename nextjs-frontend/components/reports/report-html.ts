"use client";

/** A report page that couldn't be fetched (404: not built yet). */
export class ReportError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

/**
 * The backend serves report pages at /reports/<ns>/<name>.html (proxied on this origin). They need a signed link or a
 * bearer token, and they may not be framed, so the app fetches them with the session token and then shows, opens or
 * downloads the HTML itself. Pages rendered from people's templates may not run scripts (the backend's rule), so those
 * get a script-src 'none' policy wherever the app puts them.
 */
export async function fetchReportHtml(path: string, token: string | undefined): Promise<string> {
  const res = await fetch(path, { headers: token ? { Authorization: `Bearer ${token}` } : {}, cache: "no-store" });
  if (res.status === 404) throw new ReportError(404, "There’s no report page yet. The Report step builds it after analysis.");
  if (!res.ok) throw new ReportError(res.status, `Couldn’t load the report (${res.status})`);
  return res.text();
}

/** Template reports (a "--" in the file name) are people's templates: no scripts. */
export function isTemplateReport(path: string): boolean {
  return path.split("?")[0].includes("--");
}

/**
 * Make fetched report HTML stand on its own: links resolve against this origin (they're signed as served), and
 * template reports can't run scripts. `newTab` opens links in a new tab (used inside the in-page preview).
 */
export function prepareReportHtml(html: string, opts: { scripts: boolean; newTab?: boolean; origin: string }): string {
  const head = [
    `<base href="${opts.origin}/"${opts.newTab ? ' target="_blank"' : ""}>`,
    opts.scripts ? "" : `<meta http-equiv="Content-Security-Policy" content="script-src 'none'; object-src 'none'; base-uri 'self'">`,
  ].join("");
  // Right after <head>, before anything that could run; or first thing when there's no head.
  return /<head[^>]*>/i.test(html) ? html.replace(/<head[^>]*>/i, (m) => `${m}${head}`) : `${head}${html}`;
}

export function openHtml(html: string): void {
  const url = URL.createObjectURL(new Blob([html], { type: "text/html" }));
  window.open(url, "_blank", "noopener");
  setTimeout(() => URL.revokeObjectURL(url), 60_000);
}

export function downloadHtml(html: string, filename: string): void {
  const url = URL.createObjectURL(new Blob([html], { type: "text/html" }));
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 10_000);
}

/** Print in the light palette whatever the theme ("the print styles always use the light palette"). */
export function printInLight(print: () => void = () => window.print()): void {
  const el = document.documentElement;
  const prev = el.getAttribute("data-theme");
  el.setAttribute("data-theme", "light");
  try {
    print();
  } finally {
    if (prev === null) el.removeAttribute("data-theme");
    else el.setAttribute("data-theme", prev);
  }
}

/** Ctrl/⌘+P on a report page also prints light. */
export function lightOnPrint(): () => void {
  const el = document.documentElement;
  let prev: string | null = null;
  const before = () => {
    prev = el.getAttribute("data-theme");
    el.setAttribute("data-theme", "light");
  };
  const after = () => {
    if (prev === null) el.removeAttribute("data-theme");
    else el.setAttribute("data-theme", prev);
  };
  window.addEventListener("beforeprint", before);
  window.addEventListener("afterprint", after);
  return () => {
    window.removeEventListener("beforeprint", before);
    window.removeEventListener("afterprint", after);
  };
}

/** A file name from a title: "Episode 12 — Reading…" → "episode-12-reading.html". */
export function htmlName(title: string | null | undefined, fallback = "report"): string {
  const s = (title ?? "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 60);
  return `${s || fallback}.html`;
}
