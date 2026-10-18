import type {
  Estimate,
  MessageRecord,
  Passage,
  Research,
  SandboxFile,
  SandboxOutput,
} from "@/app/openapi-client/types.gen";

/**
 * One question's answer as it streams from POST /chats/{id}/messages. The backend sends server-sent events:
 * `scoped` (a conversation over everything narrowed to the namespace it's about), `suggested` (namespaces to offer when that isn't clear), `step` (a tool the assistant used), `research` (a research
 * goal's plan, as GET /research/{id} has it), `approval` (work waiting for the person), `notice`, `passages` (the numbered
 * excerpts), `token` (answer text), `error`, `stopped` (Stop was pressed: what came is saved, marked stopped), `meta`
 * (the answer's record: what produced it) and `done` (the saved message id).
 */

export type ToolStep = {
  tool: string;
  args: Record<string, unknown>;
  summary: string;
  /** A code sandbox run's output: the start of stdout, the end of stderr. */
  output?: SandboxOutput | null;
  /** A file the code sandbox handed the person, to download from the answer. */
  file?: SandboxFile | null;
  /** How long the tool took. */
  ms?: number | null;
  /** It failed (or was unknown or blocked); the summary says why. */
  failed?: boolean | null;
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
  /** What produced the answer, once it's saved. */
  meta: MessageRecord | null;
  /** The question was a research goal: its plan, to approve (GET /research/{id} has it from then on). */
  research: Research | null;
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
    meta: null,
    research: null,
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
            output: sandboxOutput(o.output),
            file: sandboxFile(o.file),
            ms: typeof o.ms === "number" ? o.ms : null,
            failed: o.failed === true || null,
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
    case "research":
      return typeof o.id === "number" ? { ...s, research: o as unknown as Research } : s;
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
    case "meta":
      return { ...s, meta: d && !Array.isArray(d) ? (o as MessageRecord) : null };
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

function sandboxOutput(v: unknown): SandboxOutput | undefined {
  if (!v || typeof v !== "object") return undefined;
  const o = v as Record<string, unknown>;
  return {
    stdout: str(o.stdout),
    stderr: str(o.stderr),
    exit_code: typeof o.exit_code === "number" ? o.exit_code : null,
    timed_out: o.timed_out === true,
    cut: o.cut === true,
  };
}

function sandboxFile(v: unknown): SandboxFile | undefined {
  if (!v || typeof v !== "object") return undefined;
  const o = v as Record<string, unknown>;
  return typeof o.id === "string" && typeof o.name === "string"
    ? { id: o.id, name: o.name, size: typeof o.size === "number" ? o.size : 0 }
    : undefined;
}

/** Tool steps as an answer was saved with them (GET /chats/{id}), in the shape the live stream gives. */
export function savedSteps(
  steps:
    | {
        tool: string;
        args?: Record<string, unknown>;
        summary?: string;
        ms?: number | null;
        failed?: boolean | null;
        output?: SandboxOutput | null;
        file?: SandboxFile | null;
      }[]
    | null
    | undefined,
): ToolStep[] {
  return (steps ?? []).map((s) => ({
    tool: s.tool,
    args: s.args ?? {},
    summary: s.summary ?? "",
    ...(s.ms != null ? { ms: s.ms } : {}),
    ...(s.failed ? { failed: true } : {}),
    output: sandboxOutput(s.output),
    file: sandboxFile(s.file),
  }));
}

/** The files the code sandbox handed the person in these steps, to download from the answer. */
export function sharedFiles(steps: ToolStep[]): SandboxFile[] {
  return steps.flatMap((s) => (s.file ? [s.file] : []));
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
