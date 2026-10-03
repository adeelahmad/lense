import type { Estimate, Passage } from "@/app/openapi-client/types.gen";

/**
 * One question's answer as it streams from POST /chats/{id}/messages. The backend sends server-sent events:
 * `scoped` (a conversation over everything narrowed to the namespace it's about), `suggested` (namespaces to offer when that isn't clear), `step` (a tool the assistant used), `approval` (work waiting for the person), `notice`, `passages` (the numbered
 * excerpts), `token` (answer text), `error`, `stopped` (Stop was pressed: what came is saved, marked stopped) and
 * `done` (the saved message id).
 */

export type ToolStep = {
  tool: string;
  args: Record<string, unknown>;
  summary: string;
};
export type PendingApproval = {
  id: number;
  tool: string;
  summary: string;
  estimate?: Estimate | null;
};
/** A namespace offered to a conversation over everything: an existing one, or a new one to create. */
export type Suggested = { name: string; new: boolean };
export type TurnStatus = "streaming" | "done" | "error" | "stopped";

export type TurnState = {
  question: string;
  status: TurnStatus;
  steps: ToolStep[];
  approvals: PendingApproval[];
  notice: string | null;
  /** The namespaces a conversation over everything was narrowed to, chosen for the person. */
  scoped: string[] | null;
  /** Namespaces offered to pick from, when which one it's about isn't clear. */
  suggested: Suggested[] | null;
  /** null until the passages event arrives. */
  passages: Passage[] | null;
  text: string;
  error: string | null;
  messageId: number | null;
};

export function newTurn(question: string): TurnState {
  return {
    question,
    status: "streaming",
    steps: [],
    approvals: [],
    notice: null,
    scoped: null,
    suggested: null,
    passages: null,
    text: "",
    error: null,
    messageId: null,
  };
}

function parse(data: string): unknown {
  try {
    return JSON.parse(data);
  } catch {
    return null;
  }
}

const str = (v: unknown, fallback = ""): string => (typeof v === "string" ? v : fallback);

/** Apply one server-sent event to the turn. Unknown events are ignored. */
export function applyEvent(s: TurnState, ev: { event: string; data: string }): TurnState {
  const d = parse(ev.data) as Record<string, unknown> | unknown[] | null;
  const o = (d && !Array.isArray(d) ? d : {}) as Record<string, unknown>;
  switch (ev.event) {
    case "step":
      return {
        ...s,
        steps: [
          ...s.steps,
          {
            tool: str(o.tool, "tool"),
            args: (o.args as Record<string, unknown>) ?? {},
            summary: str(o.summary),
          },
        ],
      };
    case "approval":
      if (typeof o.id !== "number") return s;
      return {
        ...s,
        approvals: [
          ...s.approvals,
          {
            id: o.id,
            tool: str(o.tool),
            summary: str(o.summary),
            estimate: (o.estimate as Estimate | null) ?? null,
          },
        ],
      };
    case "scoped":
      return { ...s, scoped: Array.isArray(o.namespaces) ? o.namespaces.map(String) : null };
    case "suggested":
      return {
        ...s,
        suggested: Array.isArray(o.namespaces)
          ? (o.namespaces as Record<string, unknown>[])
              .filter((x) => x && typeof x.name === "string")
              .map((x) => ({ name: String(x.name), new: Boolean(x.new) }))
          : null,
      };
    case "notice":
      return { ...s, notice: str(o.message) || null };
    case "passages":
      return { ...s, passages: Array.isArray(d) ? (d as Passage[]) : [] };
    case "token":
      return { ...s, text: s.text + str(o.text) };
    case "error":
      return {
        ...s,
        status: "error",
        error: str(o.message, "The model didn't answer."),
      };
    case "stopped":
      return { ...s, status: "stopped" };
    case "done":
      return {
        ...s,
        status: s.status === "error" || s.status === "stopped" ? s.status : "done",
        messageId: typeof o.message === "number" ? o.message : s.messageId,
      };
    default:
      return s;
  }
}

/** Tool steps as an answer was saved with them (GET /chats/{id}), in the shape the live stream gives. */
export function savedSteps(
  steps: { tool: string; args?: Record<string, unknown>; summary?: string }[] | null | undefined,
): ToolStep[] {
  return (steps ?? []).map((s) => ({ tool: s.tool, args: s.args ?? {}, summary: s.summary ?? "" }));
}

/** A short plain-words line for a tool call, when the backend's summary is missing. */
export function stepTitle(step: ToolStep): string {
  if (step.summary) return step.summary;
  return step.tool.replace(/_/g, " ");
}

/** `search_transcripts(query="x", limit=8)`: the call as it was made. */
export function stepCall(step: ToolStep): string {
  const args = Object.entries(step.args ?? {})
    .map(([k, v]) => `${k}=${JSON.stringify(v)}`)
    .join(", ");
  return `${step.tool}(${args})`;
}
