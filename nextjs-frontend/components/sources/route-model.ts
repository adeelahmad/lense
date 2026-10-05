import type { Route, RouteView } from "@/app/openapi-client/types.gen";
import { splitPatterns } from "@/components/sources/source-model";

/** What a routing rule can look at in a message, as the backend's mail_routes.FIELDS. */
export const ROUTE_FIELDS = [
  { value: "from", label: "From", placeholder: "*@acme.com" },
  { value: "to", label: "Sent to", placeholder: "support@harbour.org" },
  { value: "subject", label: "Subject has", placeholder: "invoice, receipt" },
  { value: "list", label: "Mailing list", placeholder: "news.example.org" },
  { value: "mailbox", label: "Mailbox", placeholder: "Projects/*" },
] as const;
export type RouteField = (typeof ROUTE_FIELDS)[number]["value"];

/** "" as a target: skip the message. */
export const SKIP = "";

export type RuleCondition = { field: RouteField; patterns: string };
export type RuleForm = { key: number; conditions: RuleCondition[]; target: string | null };

let next = 1;
const key = () => next++;

export function blankRule(namespace: string | null = null): RuleForm {
  return { key: key(), conditions: [{ field: "from", patterns: "" }], target: namespace };
}

/** Saved rules as the editor shows them. A rule whose namespace was deleted has no target until one is chosen. */
export function rulesFromWatch(routes: RouteView[] | undefined): RuleForm[] {
  return (routes ?? []).map((r) => ({
    key: key(),
    conditions: Object.entries(r.match).map(([field, patterns]) => ({
      field: field as RouteField,
      patterns: patterns.join(", "),
    })),
    target: r.skip ? SKIP : (r.namespace ?? null),
  }));
}

/** The editor's rules as the API takes them; conditions with no patterns are left out. */
export function rulesToApi(rules: RuleForm[]): Route[] {
  return rules.map((r) => {
    const match: Record<string, string[]> = {};
    for (const c of r.conditions) {
      const p = splitPatterns(c.patterns);
      if (p.length) match[c.field] = [...(match[c.field] ?? []), ...p];
    }
    return r.target === SKIP ? { match, skip: true } : { match, namespace: r.target };
  });
}

/** What's wrong with a rule, or null: it needs a pattern and somewhere to send the mail. */
export function ruleProblem(rule: RuleForm): string | null {
  if (!rule.conditions.some((c) => splitPatterns(c.patterns).length)) return "Add a pattern to match.";
  if (rule.target === null) return "Choose a namespace, or skip.";
  return null;
}

export function move<T>(list: T[], from: number, to: number): T[] {
  if (to < 0 || to >= list.length) return list;
  const out = [...list];
  const [item] = out.splice(from, 1);
  out.splice(to, 0, item);
  return out;
}
