import type { Estimate, Passage } from "@/app/openapi-client/types.gen";

/**
 * One question's answer as it streams from POST /chats/{id}/messages. The backend sends server-sent events:
 * `step` (a tool the assistant used), `approval` (work waiting for the person), `notice`, `passages` (the numbered
 * excerpts), `token` (answer text), `error` and `done` (the saved message id).
 */

export type ToolStep = { tool: string; args: Record<string, unknown>; summary: string };
export type PendingApproval = { id: number; tool: string; summary: string; estimate?: Estimate | null };
export type TurnStatus = "streaming" | "done" | "error" | "stopped";

export type TurnState = {
  question: string;
  status: TurnStatus;
  steps: ToolStep[];
  approvals: PendingApproval[];
  notice: string | null;
  /** null until the passages event arrives. */
  passages: Passage[] | null;
  text: string;
  error: string | null;
  messageId: number | null;
};

export function newTurn(question: string): TurnState {
  return { question, status: "streaming", steps: [], approvals: [], notice: null, passages: null, text: "", error: null, messageId: null };
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
      return { ...s, steps: [...s.steps, { tool: str(o.tool, "tool"), args: (o.args as Record<string, unknown>) ?? {}, summary: str(o.summary) }] };
    case "approval":
      if (typeof o.id !== "number") return s;
      return { ...s, approvals: [...s.approvals, { id: o.id, tool: str(o.tool), summary: str(o.summary), estimate: (o.estimate as Estimate | null) ?? null }] };
    case "notice":
      return { ...s, notice: str(o.message) || null };
    case "passages":
      return { ...s, passages: Array.isArray(d) ? (d as Passage[]) : [] };
    case "token":
      return { ...s, text: s.text + str(o.text) };
    case "error":
      return { ...s, status: "error", error: str(o.message, "The model didn't answer.") };
    case "done":
      return { ...s, status: s.status === "error" ? "error" : "done", messageId: typeof o.message === "number" ? o.message : s.messageId };
    default:
      return s;
  }
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
