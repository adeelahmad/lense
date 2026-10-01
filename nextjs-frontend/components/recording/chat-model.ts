/**
 * The recording's chat tab: one question's streamed answer (POST /chats/{id}/messages sends step, approval, notice,
 * passages, token, error and done events) and answer text split around its [n] citations. Pure functions.
 */

export type Passage = {
  n: number;
  recording_id: number;
  t0?: number | null;
  /** When it was said ("12:34"), or for a document its page ("p. 3"). */
  time?: string | null;
  /** A document's or an image's page it's on (from 0). */
  page?: number | null;
  speaker?: string | null;
  title?: string | null;
  text: string;
};

export type Answer = {
  question: string;
  status: "streaming" | "done" | "error" | "stopped";
  text: string;
  passages: Passage[];
  notice: string | null;
  error: string | null;
  steps: string[];
  /** The server saved it (it's in the conversation now). */
  saved?: boolean;
};

export const newAnswer = (question: string): Answer => ({
  question,
  status: "streaming",
  text: "",
  passages: [],
  notice: null,
  error: null,
  steps: [],
});

function parse(data: string): unknown {
  try {
    return JSON.parse(data);
  } catch {
    return null;
  }
}

export function applyChatEvent(a: Answer, ev: { event: string; data: string }): Answer {
  const d = parse(ev.data);
  const o = (d && typeof d === "object" && !Array.isArray(d) ? d : {}) as Record<string, unknown>;
  switch (ev.event) {
    case "token":
      return {
        ...a,
        text: a.text + (typeof o.text === "string" ? o.text : ""),
      };
    case "passages":
      return { ...a, passages: Array.isArray(d) ? (d as Passage[]) : [] };
    case "notice":
      return {
        ...a,
        notice: typeof o.message === "string" ? o.message : a.notice,
      };
    case "step":
      return {
        ...a,
        steps: [...a.steps, typeof o.summary === "string" && o.summary ? o.summary : String(o.tool ?? "tool")],
      };
    case "error":
      return {
        ...a,
        status: "error",
        error: typeof o.message === "string" ? o.message : "The model didn't answer.",
      };
    case "stopped":
      return { ...a, status: "stopped" };
    case "done":
      return {
        ...a,
        status: a.status === "error" || a.status === "stopped" ? a.status : "done",
        saved: typeof o.message === "number" || a.saved,
      };
    default:
      return a;
  }
}

export type Part = { kind: "text"; text: string } | { kind: "cite"; n: number };

/** "…nine days [1][2]." → text and citation parts ([1], [1, 2] and [1][2] all work). */
export function citeParts(text: string): Part[] {
  const out: Part[] = [];
  const re = /\[(\d+(?:\s*,\s*\d+)*)\]/g;
  let last = 0;
  let m: RegExpExecArray | null;
  while ((m = re.exec(text))) {
    if (m.index > last) out.push({ kind: "text", text: text.slice(last, m.index) });
    for (const n of m[1].split(",")) out.push({ kind: "cite", n: Number(n.trim()) });
    last = re.lastIndex;
  }
  if (last < text.length) out.push({ kind: "text", text: text.slice(last) });
  return out;
}

/** Whether a conversation is this recording's own: its scope is exactly this one recording. */
export function isRecordingChat(scope: unknown, id: number): boolean {
  const s = (scope && typeof scope === "object" ? scope : {}) as {
    recordings?: unknown;
    namespaces?: unknown;
    speakers?: unknown;
  };
  return (
    Array.isArray(s.recordings) &&
    s.recordings.length === 1 &&
    Number(s.recordings[0]) === id &&
    !(Array.isArray(s.speakers) && s.speakers.length)
  );
}
