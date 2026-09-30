import { apiBaseUrl } from "@/lib/api/client";

/**
 * Serves the FastAPI backend's paths on this origin: /api/v1, /embed, /iiif, /reports and /static.
 *
 * The API hands out relative signed media links (`/api/v1/recordings/12/audio?exp=..&sig=..`), so proxying them
 * makes <audio>, <video> and <img> work, and lets the browser call the API (including server-sent event streams)
 * without CORS. It runs per request in Node, so API_BASE_URL is read at runtime and one build works anywhere.
 * Bodies stream both ways; byte ranges and SSE pass straight through.
 */
const HOP = new Set(["connection", "keep-alive", "transfer-encoding", "te", "upgrade", "proxy-connection", "host"]);

async function proxy(req: Request): Promise<Response> {
  const incoming = new URL(req.url);
  const target = new URL(incoming.pathname + incoming.search, apiBaseUrl());
  const headers = new Headers();
  req.headers.forEach((v, k) => {
    if (!HOP.has(k)) headers.set(k, v);
  });
  headers.set("x-forwarded-host", incoming.host);
  headers.set("x-forwarded-proto", incoming.protocol.replace(":", ""));
  // Ask for identity so byte ranges and lengths stay exact.
  headers.set("accept-encoding", "identity");

  const hasBody = !["GET", "HEAD"].includes(req.method);
  let upstream: Response;
  try {
    upstream = await fetch(target, {
      method: req.method,
      headers,
      body: hasBody ? req.body : undefined,
      redirect: "manual",
      cache: "no-store",
      signal: req.signal,
      // Required by Node's fetch to stream a request body.
      ...(hasBody ? { duplex: "half" } : {}),
    } as RequestInit);
  } catch {
    return Response.json({ detail: "Can't reach the archive server." }, { status: 502 });
  }
  const out = new Headers();
  upstream.headers.forEach((v, k) => {
    if (!HOP.has(k) && k !== "content-encoding") out.set(k, v);
  });
  return new Response(upstream.body, { status: upstream.status, statusText: upstream.statusText, headers: out });
}

export const handlers = { GET: proxy, HEAD: proxy, POST: proxy, PUT: proxy, PATCH: proxy, DELETE: proxy, OPTIONS: proxy };
