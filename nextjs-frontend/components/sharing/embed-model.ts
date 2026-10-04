import { plural } from "@/lib/format";

/**
 * Share links and embeds: the player's address (/embed/<id>, with the share token and start time), the iframe
 * snippet, start times typed as m:ss, whether a site's origin may frame the player (server.embed_frame_ancestors,
 * a CSP frame-ancestors list), and how a link has been used (plays, the sites that embed it).
 */

export const SIZES = {
  360: { width: 360, height: 520 },
  720: { width: 720, height: 420 },
} as const;
export type Size = keyof typeof SIZES;

/** The backend accepts share links for 1 to 3650 days. */
export const MAX_DAYS = 3650;

/** "14:02" → 842, "1:02:03" → 3723, "842" → 842, "" → 0; null when it isn't a time. */
export function parseStart(text: string): number | null {
  const t = text.trim();
  if (!t) return 0;
  if (/^\d+(\.\d+)?$/.test(t)) return Math.floor(Number(t));
  const m = /^(?:(\d+):)?([0-5]?\d):([0-5]\d)$/.exec(t);
  if (!m) return null;
  return Number(m[1] ?? 0) * 3600 + Number(m[2]) * 60 + Number(m[3]);
}

export function formatStart(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const x = String(s % 60).padStart(2, "0");
  return h ? `${h}:${String(m).padStart(2, "0")}:${x}` : `${m}:${x}`;
}

/** The public player address for a share token (works without signing in). */
export function embedUrl(
  origin: string,
  recordingId: number,
  opts: { token?: string | null; start?: number | null } = {},
): string {
  const q = new URLSearchParams();
  if (opts.token) q.set("s", opts.token);
  if (opts.start) q.set("t", String(Math.floor(opts.start)));
  const qs = q.toString();
  return `${origin.replace(/\/$/, "")}/embed/${recordingId}${qs ? `?${qs}` : ""}`;
}

/** Add or change the start time on a player address (signed links keep their signature: t isn't signed). */
export function withStart(url: string, start: number | null | undefined): string {
  const [path, query = ""] = url.split("?");
  const q = new URLSearchParams(query);
  if (start) q.set("t", String(Math.floor(start)));
  else q.delete("t");
  const qs = q.toString();
  return qs ? `${path}?${qs}` : path;
}

const esc = (s: string) => s.replace(/&/g, "&amp;").replace(/"/g, "&quot;").replace(/</g, "&lt;");
/** URLs keep a plain "&" between parameters, as embed snippets usually do (browsers read both). */
const escUrl = (s: string) => s.replace(/"/g, "%22").replace(/</g, "%3C");

/** The iframe snippet people paste into their site. */
export function iframeSnippet({ src, size, title }: { src: string; size: Size; title: string }): string {
  const { width, height } = SIZES[size];
  return `<iframe src="${escUrl(src)}"\n  width="${width}" height="${height}" title="Lens player: ${esc(title)}"\n  loading="lazy" style="border:0"></iframe>`;
}

/** "blog.example.com" or "https://blog.example.com/post" → "https://blog.example.com"; null when it isn't a site. */
export function normalizeOrigin(input: string): string | null {
  const t = input.trim();
  if (!t) return null;
  try {
    const u = new URL(/^[a-z][a-z0-9+.-]*:\/\//i.test(t) ? t : `https://${t}`);
    if (!["http:", "https:"].includes(u.protocol) || (!u.hostname.includes(".") && u.hostname !== "localhost"))
      return null;
    return u.origin;
  } catch {
    return null;
  }
}

const DEFAULT_PORT: Record<string, string> = { "http:": "80", "https:": "443" };

/** Whether a CSP frame-ancestors list lets `origin` frame the player. */
export function originAllowed(origin: string, ancestors: string[], selfOrigin: string): boolean {
  let o: URL;
  try {
    o = new URL(origin);
  } catch {
    return false;
  }
  const port = o.port || DEFAULT_PORT[o.protocol] || "";
  for (const raw of ancestors) {
    const src = raw.trim();
    if (!src || src === "'none'") continue;
    if (src === "*") return true;
    if (src === "'self'") {
      if (new URL(selfOrigin).origin === o.origin) return true;
      continue;
    }
    if (/^[a-z][a-z0-9+.-]*:$/i.test(src)) {
      if (o.protocol === src.toLowerCase() || (src.toLowerCase() === "http:" && o.protocol === "https:")) return true;
      continue;
    }
    const m = /^(?:([a-z][a-z0-9+.-]*):\/\/)?(\*\.)?([^/:]+)(?::(\d+|\*))?/i.exec(src);
    if (!m) continue;
    const [, scheme, wild, host, p] = m;
    if (
      scheme &&
      `${scheme.toLowerCase()}:` !== o.protocol &&
      !(scheme.toLowerCase() === "http" && o.protocol === "https:")
    )
      continue;
    const h = o.hostname.toLowerCase();
    const want = host.toLowerCase();
    if (wild ? !h.endsWith(`.${want}`) : h !== want) continue;
    if (p && p !== "*" && p !== port) continue;
    if (
      !p &&
      scheme &&
      port !== DEFAULT_PORT[`${scheme.toLowerCase()}:`] &&
      !(scheme.toLowerCase() === "http" && port === "443")
    )
      continue;
    return true;
  }
  return false;
}

/** "30 Oct 2026" for an expiry `days` from now. */
export function expiryDate(days: number, now = new Date()): Date {
  return new Date(now.getTime() + days * 86_400_000);
}

export function fmtDay(d: Date | string | null | undefined): string {
  if (!d) return "—";
  const x = typeof d === "string" ? new Date(d) : d;
  if (Number.isNaN(x.getTime())) return "—";
  return `${x.getDate()} ${["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"][x.getMonth()]} ${x.getFullYear()}`;
}

/** Whether a share link still works, or why not. */
export type LinkState = "active" | "expired" | "revoked";

export function linkState(s: { active: boolean; revoked?: boolean | null }): LinkState {
  return s.active ? "active" : s.revoked ? "revoked" : "expired";
}

/** "https://blog.example.org" → "blog.example.org" (a port that isn't the default stays). */
export function siteName(origin: string): string {
  try {
    return new URL(origin).host || origin;
  } catch {
    return origin;
  }
}

/** "12 plays · last 3 Oct 2026", or "Not played yet". */
export function playsLine(plays: number | null | undefined, playedAt?: string | null): string {
  if (!plays) return "Not played yet";
  return `${plural(plays, "play")}${playedAt ? ` · last ${fmtDay(playedAt)}` : ""}`;
}

/** "Embedded on blog.example.org (2 opens), news.example.com and 3 more sites"; null when no site has framed it. */
export function embeddedLine(
  sites: { origin: string; opens?: number | null }[] | null | undefined,
  max = 2,
): string | null {
  if (!sites?.length) return null;
  const named = sites
    .slice(0, max)
    .map((x) => `${siteName(x.origin)}${x.opens && x.opens > 1 ? ` (${plural(x.opens, "open")})` : ""}`);
  const rest = sites.length - named.length;
  return `Embedded on ${named.join(", ")}${rest ? ` and ${plural(rest, "more site")}` : ""}`;
}
