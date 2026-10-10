// A stand-in OpenAI-compatible model server, so the journeys that need a model (the install wizard finding one, the
// setup wizard testing one, the assistant answering) run without a real one. Every answer is the same short reply.
import http from "node:http";

const REPLY = "This is the survey's stand-in model answering.";
const MODELS = ["survey-chat", "survey-embed"];

export function startFakeModel(port) {
  const server = http.createServer((req, res) => {
    let body = "";
    req.on("data", (c) => (body += c));
    req.on("end", () => {
      const url = req.url.replace(/^\/v1/, "");
      const json = (code, data) => {
        res.writeHead(code, { "Content-Type": "application/json" });
        res.end(JSON.stringify(data));
      };
      if (req.method === "GET" && url.startsWith("/models"))
        return json(200, { object: "list", data: MODELS.map((id) => ({ id, object: "model" })) });
      if (req.method === "GET" && url.startsWith("/api/tags")) return json(200, { models: MODELS.map((name) => ({ name })) });
      let payload = {};
      try {
        payload = JSON.parse(body || "{}");
      } catch {
        /* an empty answer below */
      }
      if (url.startsWith("/embeddings")) {
        const inputs = Array.isArray(payload.input) ? payload.input : [payload.input ?? ""];
        return json(200, {
          object: "list",
          model: "survey-embed",
          data: inputs.map((_, index) => ({ object: "embedding", index, embedding: Array.from({ length: 8 }, (_, i) => (i + 1) / 8) })),
        });
      }
      if (url.startsWith("/chat/completions")) {
        const model = payload.model || MODELS[0];
        if (payload.stream) {
          res.writeHead(200, { "Content-Type": "text/event-stream" });
          const chunk = (delta, finish = null) =>
            `data: ${JSON.stringify({ id: "survey", object: "chat.completion.chunk", model, choices: [{ index: 0, delta, finish_reason: finish }] })}\n\n`;
          res.write(chunk({ role: "assistant", content: REPLY }));
          res.write(chunk({}, "stop"));
          return res.end("data: [DONE]\n\n");
        }
        // structured output: an empty object satisfies most schemas' optional fields
        const content = payload.response_format ? "{}" : REPLY;
        return json(200, {
          id: "survey",
          object: "chat.completion",
          model,
          choices: [{ index: 0, message: { role: "assistant", content }, finish_reason: "stop" }],
          usage: { prompt_tokens: 1, completion_tokens: 1, total_tokens: 2 },
        });
      }
      json(404, { error: "not found" });
    });
  });
  return new Promise((resolve) => server.listen(port, "127.0.0.1", () => resolve(server)));
}
