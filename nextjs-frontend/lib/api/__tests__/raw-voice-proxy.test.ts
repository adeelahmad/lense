/** @jest-environment node */
import { handlers } from "../backend-proxy";

jest.mock("@/lib/api/client", () => ({ apiBaseUrl: () => "http://backend.test" }));

it("lets the backend decide raw voice authentication without injecting credentials", async () => {
  const fetcher = jest.spyOn(globalThis, "fetch").mockResolvedValue(new Response("denied", { status: 401 }));
  const body = JSON.stringify({ text: " # exact text ", server_only: true });
  const response = await handlers.POST(new Request("https://lens.test/api/v1/voice/speak", {
    method: "POST", headers: { "Content-Type": "application/json" }, body,
  }));
  const [url, options] = fetcher.mock.calls[0];
  expect(String(url)).toBe("http://backend.test/api/v1/voice/speak");
  expect((options?.headers as Headers).has("authorization")).toBe(false);
  expect(await new Response(options?.body).text()).toBe(body);
  expect(response.status).toBe(401);
});

it("streams raw media and preserves provider errors and retry guidance", async () => {
  const fetcher = jest.spyOn(globalThis, "fetch").mockResolvedValue(Response.json(
    { detail: "Voice service is busy" }, { status: 429, headers: { "Retry-After": "5" } },
  ));
  const response = await handlers.POST(new Request("https://lens.test/api/v1/voice/transcribe", {
    method: "POST", headers: { "Content-Type": "video/webm" }, body: "raw-media-bytes",
  }));
  const options = fetcher.mock.calls[0][1];
  expect((options?.headers as Headers).get("content-type")).toBe("video/webm");
  expect(await new Response(options?.body).text()).toBe("raw-media-bytes");
  expect(response.status).toBe(429);
  expect(response.headers.get("retry-after")).toBe("5");
  expect(await response.json()).toEqual({ detail: "Voice service is busy" });
});
