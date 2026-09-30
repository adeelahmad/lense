/**
 * Server-Sent Events over fetch, for the backend's streaming endpoints
 * (GET /api/v1/events for job progress, POST /api/v1/chats/{id}/messages).
 * Unlike EventSource this can send an Authorization header and a POST body.
 *
 *   const { data: session } = useSession();
 *   for await (const msg of streamSSE("/api/v1/events", { accessToken: session.accessToken, signal })) {
 *     const payload = JSON.parse(msg.data);
 *   }
 *
 * Paths are relative to the frontend origin, which proxies /api/v1 to the backend.
 */

export type SSEMessage = {
  /** The `event:` field; "message" when absent. */
  event: string;
  data: string;
  id?: string;
  retry?: number;
};

export type StreamSSEOptions = {
  accessToken?: string;
  method?: "GET" | "POST";
  /** Sent as JSON. */
  body?: unknown;
  headers?: HeadersInit;
  signal?: AbortSignal;
  /** Called once the server has accepted the stream (before any message arrives). */
  onOpen?: () => void;
};

export class SSEError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
    this.name = "SSEError";
  }
}

/** Incremental parser: feed text chunks, get complete messages back. */
export function createSSEParser() {
  let buffer = "";
  let data: string[] = [];
  let event = "";
  let id: string | undefined;
  let retry: number | undefined;
  let pendingCR = false;

  function line(raw: string, out: SSEMessage[]) {
    if (raw === "") {
      if (data.length)
        out.push({
          event: event || "message",
          data: data.join("\n"),
          id,
          retry,
        });
      data = [];
      event = "";
      retry = undefined;
      return;
    }
    if (raw.startsWith(":")) return; // comment / keep-alive
    const colon = raw.indexOf(":");
    const field = colon === -1 ? raw : raw.slice(0, colon);
    let value = colon === -1 ? "" : raw.slice(colon + 1);
    if (value.startsWith(" ")) value = value.slice(1);
    if (field === "data") data.push(value);
    else if (field === "event") event = value;
    else if (field === "id") id = value;
    else if (field === "retry" && /^\d+$/.test(value)) retry = Number(value);
  }

  return {
    push(chunk: string): SSEMessage[] {
      // A "\r\n" split across chunks: the "\r" already ended the line.
      if (pendingCR && chunk.startsWith("\n")) chunk = chunk.slice(1);
      pendingCR = chunk.endsWith("\r");
      buffer += chunk;
      const out: SSEMessage[] = [];
      const lines = buffer.split(/\r\n|\r|\n/);
      buffer = lines.pop() ?? "";
      for (const l of lines) line(l, out);
      return out;
    },
  };
}

export async function* streamSSE(
  path: string,
  { accessToken, method = "GET", body, headers, signal, onOpen }: StreamSSEOptions = {},
): AsyncGenerator<SSEMessage> {
  const h = new Headers(headers);
  h.set("Accept", "text/event-stream");
  if (accessToken) h.set("Authorization", `Bearer ${accessToken}`);
  if (body !== undefined) h.set("Content-Type", "application/json");

  const response = await fetch(path, {
    method,
    headers: h,
    body: body === undefined ? undefined : JSON.stringify(body),
    signal,
    cache: "no-store",
  });
  if (!response.ok || !response.body) {
    throw new SSEError(response.status, `Stream ${path} failed with status ${response.status}`);
  }
  onOpen?.();

  const parser = createSSEParser();
  const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      yield* parser.push(value);
    }
    yield* parser.push("\n\n");
  } finally {
    // Stops the download when the consumer breaks out early.
    await reader.cancel().catch(() => undefined);
  }
}
