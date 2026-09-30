/**
 * The audit log (Admin AD4): the backend's action names grouped for filtering, targets and details in plain words,
 * filtering, and CSV export. The backend records who, what and on what; secrets are never in it.
 */
import { FIELDS, SECTIONS } from "@/components/settings/model";

export type AuditEntry = { at: string; email?: string | null; action: string; target?: string | null; detail?: unknown };
export type Group = "access" | "settings" | "sources" | "sharing" | "content" | "other";
export const GROUPS: { value: Group; label: string }[] = [
  { value: "access", label: "Access" },
  { value: "settings", label: "Settings" },
  { value: "sources", label: "Sources" },
  { value: "sharing", label: "Sharing" },
  { value: "content", label: "Content" },
  { value: "other", label: "Other" },
];

const PREFIX: [RegExp, Group][] = [
  [/^(setup|password\.|user\.|member\.|token\.|email$)/, "access"],
  [/^(settings\.|search\.|pipeline\.|template\.|namespace\.|faces\.mode)/, "settings"],
  [/^(source\.|watch\.|import)/, "sources"],
  [/^(share\.|embed)/, "sharing"],
  [/^(metadata\.|speaker\.|entity\.|mention\.|transcript\.|ocr\.|face\.|faces\.|batch\.|assistant\.|recording\.|collection\.)/, "content"],
];

export function actionGroup(action: string): Group {
  return PREFIX.find(([rx]) => rx.test(action))?.[1] ?? "other";
}

export type Lookup = { people: Record<string, string>; accounts: Record<number, string>; namespaces: Record<number, string> };

const SECTION_LABEL: Record<string, string> = Object.fromEntries(SECTIONS.flatMap((s) => s.backend.map((b) => [b, s.label])));

/** "Settings · Access & embedding", "Recording 12", a person's name, a namespace. */
export function targetText(target: string | null | undefined, action: string, look: Lookup): string {
  if (!target) return "—";
  if (action.startsWith("settings.")) return `Settings · ${SECTION_LABEL[target] ?? target}`;
  const m = target.match(/^([a-z_]+):(\d+)$/);
  if (m) {
    const id = Number(m[2]);
    switch (m[1]) {
      case "recording":
        return `Recording ${id}`;
      case "account":
        return look.accounts[id] ?? `Account ${id}`;
      case "space":
        return look.namespaces[id] ?? `Namespace ${id}`;
      case "api_token":
        return `API token ${id}`;
      default:
        return `${m[1].replace(/_/g, " ")} ${id}`;
    }
  }
  return target;
}

/** Where a target lives in the app, when it has a page. */
export function targetHref(target: string | null | undefined, action: string, look: Lookup): string | null {
  if (!target) return null;
  if (action.startsWith("settings.")) {
    const s = SECTIONS.find((x) => x.backend.includes(target));
    return s ? `/settings/${s.id}` : null;
  }
  const m = target.match(/^([a-z_]+):(\d+)$/);
  if (!m) return /^https?:\/\//.test(target) ? target : null;
  if (m[1] === "recording") return `/recordings/${m[2]}`;
  if (m[1] === "space" && look.namespaces[Number(m[2])]) return `/admin/namespaces/${look.namespaces[Number(m[2])]}`;
  return null;
}

function settingLabel(section: string, key: string): string {
  const f = FIELDS.find((x) => x.section === section && (x.key === key || x.key.startsWith(`${key}.`)));
  if (!f) return key.replace(/_/g, " ");
  return f.key === key ? f.label.replace(/ \(.*\)$/, "") : key.replace(/_/g, " ");
}

function listOf(v: unknown): string[] {
  return Array.isArray(v) ? v.map(String) : [];
}

/** The details column: what changed, in plain words. Secrets appear as "(changed)", never values. */
export function detailText(e: AuditEntry, look: Lookup): string {
  const d = e.detail;
  if (d == null || (typeof d === "object" && !Array.isArray(d) && !Object.keys(d as object).length)) {
    if (e.action === "user.update") return "New password set; their sessions ended";
    if (e.action === "user.create") return "Account created";
    if (e.action === "setup") return "First admin created";
    return "";
  }
  switch (e.action) {
    case "settings.save":
      return listOf(d)
        .map((k) => (k === "api_key" ? "API key (changed)" : settingLabel(e.target ?? "", k)))
        .join(", ");
    case "member.set": {
      const x = d as { account?: number; role?: string | null };
      const who = (x.account != null && look.accounts[x.account]) || `account ${x.account}`;
      return x.role ? `${who} → ${x.role}` : `${who} removed`;
    }
    case "user.update": {
      const x = d as { name?: string; admin?: boolean; disabled?: boolean };
      const parts: string[] = [];
      if (x.name !== undefined) parts.push(`renamed to “${x.name}”`);
      if (x.admin !== undefined) parts.push(x.admin ? "made platform admin" : "no longer platform admin");
      if (x.disabled !== undefined) parts.push(x.disabled ? "disabled" : "enabled");
      return parts.join("; ") || "Updated";
    }
    case "metadata.save":
      return listOf(d).join(", ");
    case "metadata.bulk": {
      const x = d as { recordings?: number; changed?: number; fields?: string[] };
      return `${x.changed ?? 0} of ${x.recordings ?? 0} recordings · ${listOf(x.fields).join(", ")}`;
    }
  }
  if (Array.isArray(d)) return d.map(String).join(", ");
  if (typeof d === "object")
    return Object.entries(d as Record<string, unknown>)
      .map(([k, v]) => `${k.replace(/_/g, " ")}: ${typeof v === "object" ? JSON.stringify(v) : String(v)}`)
      .join("; ");
  return String(d);
}

export function personText(email: string | null | undefined, look: Lookup): string {
  if (!email) return "System";
  return look.people[email.toLowerCase()] ?? email;
}

export type Filters = { person: string | null; groups: Group[]; sinceDays: number | null };

export function filterEntries(entries: AuditEntry[], f: Filters, now = Date.now()): AuditEntry[] {
  const since = f.sinceDays ? now - f.sinceDays * 86_400_000 : null;
  return entries.filter(
    (e) =>
      (!f.person || (e.email ?? "").toLowerCase() === f.person.toLowerCase()) &&
      (!f.groups.length || f.groups.includes(actionGroup(e.action))) &&
      (since == null || Date.parse(e.at) >= since),
  );
}

function csvCell(v: string): string {
  return /[",\n]/.test(v) ? `"${v.replace(/"/g, '""')}"` : v;
}

export function toCsv(entries: AuditEntry[], look: Lookup): string {
  const rows = [["time", "person", "email", "action", "target", "details"]];
  for (const e of entries) rows.push([e.at, personText(e.email, look), e.email ?? "", e.action, targetText(e.target, e.action, look), detailText(e, look)]);
  return rows.map((r) => r.map(csvCell).join(",")).join("\n") + "\n";
}
