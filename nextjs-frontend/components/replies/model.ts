import type { Reply, ReplyEmail, ReplySource } from "@/app/openapi-client/types.gen";
import { recordingHref } from "@/components/search/links";

/** A draft's citations, as the backend writes them (responder.CITE_RX). */
const CITE_RX = /\s?\[(\d+)\]/g;
const CONFIRM_RX = /\[to confirm:[^\]\n]*\]/gi;
const ADDRESS_RX = /^[^\s@<>,;]+@[^\s@<>,;]+\.[^\s@<>,;]+$/;

/** The draft as it would be sent: without its citations (they open your archive, which the person you answer can't);
 * what responder.plain does, so Copy and the mail app get what the API's `plain` would say for an edit not saved yet. */
export function plainText(text: string): string {
  return text
    .replace(CITE_RX, "")
    .replace(/[ \t]+([.,;:!?])/g, "$1")
    .trim();
}

/** How many "[to confirm: …]" marks are still in the draft. */
export function unconfirmed(text: string): number {
  return (text.match(CONFIRM_RX) ?? []).length;
}

/** The addresses typed in a To or Cc box ("a@x.io, b@y.org"), and the first that isn't one. */
export function addresses(typed: string): { list: string[]; wrong: string | null } {
  const list = typed
    .split(/[,;\s]+/)
    .map((a) => a.trim())
    .filter(Boolean);
  return { list, wrong: list.find((a) => !ADDRESS_RX.test(a)) ?? null };
}

/** Your mail app, with the reply in it: the plain text, to, cc and subject. Sending it is yours. */
export function mailtoHref(r: { to: string[]; cc: string[]; subject: string; text: string }): string {
  const q = [
    r.cc.length ? `cc=${encodeURIComponent(r.cc.join(","))}` : "",
    `subject=${encodeURIComponent(r.subject)}`,
    `body=${encodeURIComponent(plainText(r.text))}`,
  ].filter(Boolean);
  return `mailto:${r.to.map(encodeURIComponent).join(",")}?${q.join("&")}`;
}

/** Where a source opens in the archive: a document's page, or a moment. */
export function sourceHref(s: Pick<ReplySource, "recording_id" | "page" | "t0">): string {
  return s.page != null ? `/resources/${s.recording_id}?page=${s.page + 1}` : recordingHref(s.recording_id, s.t0);
}

/** A job is on it, so the page looks again until it's done. */
export function busy(r: Pick<Reply, "status"> | undefined): boolean {
  return r?.status === "drafting";
}

/** The email asked for first (`?email=`), then the rest, newest first, as listed. */
export function ordered(emails: ReplyEmail[], first: number | null, hidden: Set<number>): ReplyEmail[] {
  const left = emails.filter((e) => !hidden.has(e.recording));
  return first ? [...left.filter((e) => e.recording === first), ...left.filter((e) => e.recording !== first)] : left;
}

/** Whether a resource is an email Lens can draft a reply to (responder.is_email). */
export function isEmail(rec: { email?: unknown; mail?: unknown; path?: string | null } | undefined): boolean {
  return Boolean(rec && (rec.email || rec.mail || /\.eml$/i.test(rec.path ?? "")));
}

/** One line on where a reply stands, under its email. */
export function statusLine(r: Reply): string {
  switch (r.status) {
    case "drafting":
      return "Drafting a reply from your archive…";
    case "failed":
      return `Couldn’t draft it${r.error ? `: ${r.error}` : "."}`;
    case "skipped":
      return `Not drafted, as mail not to answer${r.why ? ` (${r.why})` : ""}.`;
    case "dismissed":
      return "Set aside.";
    case "saved":
      return "Kept in Gmail’s drafts.";
    case "sent":
      return r.sent?.by ? `Sent by ${r.sent.by}.` : "Sent.";
    case "draft":
      return r.unconfirmed ? `Draft ready, ${r.unconfirmed} to confirm` : "Draft ready";
    default:
      return "No reply yet";
  }
}
