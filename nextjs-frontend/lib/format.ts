/** Formatting shared by every screen: times inside recordings, dates, sizes, counts. */

/** Milliseconds → "m:ss" or "h:mm:ss" (time inside a recording). */
export function tc(ms: number | null | undefined): string {
  const s = Math.max(0, Math.floor((ms ?? 0) / 1000));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const x = s % 60;
  return h ? `${h}:${String(m).padStart(2, "0")}:${String(x).padStart(2, "0")}` : `${m}:${String(x).padStart(2, "0")}`;
}

const rtf = typeof Intl !== "undefined" ? new Intl.RelativeTimeFormat("en", { numeric: "auto" }) : null;

/** "3 min ago", "in 2 days". */
export function relative(iso: string | null | undefined, now = Date.now()): string {
  if (!iso) return "—";
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return "—";
  const diff = (t - now) / 1000;
  const abs = Math.abs(diff);
  const units: [Intl.RelativeTimeFormatUnit, number][] = [
    ["second", 60],
    ["minute", 3600],
    ["hour", 86400],
    ["day", 604800],
    ["week", 2629800],
    ["month", 31557600],
    ["year", Infinity],
  ];
  const size: Record<string, number> = { second: 1, minute: 60, hour: 3600, day: 86400, week: 604800, month: 2629800, year: 31557600 };
  if (abs < 45) return "just now";
  for (const [unit, limit] of units) {
    if (abs < limit) return rtf ? rtf.format(Math.round(diff / size[unit]), unit) : `${Math.round(abs / size[unit])} ${unit}s`;
  }
  return "—";
}

/** "12 Sep 2026", or "Today, 09:02" / "Yesterday, 17:40" for recent dates. */
export function shortDate(iso: string | null | undefined, withTime = false): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  const today = new Date();
  const y = new Date(today);
  y.setDate(today.getDate() - 1);
  const time = d.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
  if (d.toDateString() === today.toDateString()) return `Today, ${time}`;
  if (d.toDateString() === y.toDateString()) return `Yesterday, ${time}`;
  const date = d.toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });
  return withTime ? `${date}, ${time}` : date;
}

/** Full timestamp in the viewer's time zone, for tooltips. */
export function absolute(iso: string | null | undefined): string {
  if (!iso) return "";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "" : d.toLocaleString("en-GB", { dateStyle: "medium", timeStyle: "short" });
}

export function hours(ms: number | null | undefined): string {
  const h = (ms ?? 0) / 3.6e6;
  return h >= 10 ? `${Math.round(h)} h` : `${h.toFixed(1)} h`;
}

export function count(n: number | null | undefined): string {
  return (n ?? 0).toLocaleString("en-US");
}

export function bytes(n: number | null | undefined): string {
  let v = n ?? 0;
  for (const u of ["B", "KB", "MB", "GB", "TB"]) {
    if (v < 1024 || u === "TB") return `${u === "B" ? v : v.toFixed(1)} ${u}`;
    v /= 1024;
  }
  return `${v}`;
}

export function initials(name: string | null | undefined): string {
  const parts = (name || "?").trim().split(/[\s@._-]+/).filter(Boolean);
  return ((parts[0]?.[0] ?? "?") + (parts.length > 1 ? parts[parts.length - 1][0] : "")).toUpperCase();
}

export function plural(n: number, one: string, many = `${one}s`): string {
  return `${count(n)} ${n === 1 ? one : many}`;
}
