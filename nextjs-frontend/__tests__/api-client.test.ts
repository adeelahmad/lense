/**
 * @jest-environment node
 */
import { Auth } from "@/app/openapi-client";
import { createClient, createConfig } from "@/app/openapi-client/client";
import { throwingNetworkErrors } from "@/lib/api/client";

function clientWith(fetchImpl: typeof fetch) {
  return throwingNetworkErrors(createClient(createConfig({ baseUrl: "http://api.test", fetch: fetchImpl })));
}

describe("the API client", () => {
  it("rejects when the API can't be reached", async () => {
    const client = clientWith(() => Promise.reject(new TypeError("fetch failed")));
    await expect(Auth.status({ client })).rejects.toThrow("fetch failed");
  });

  it("returns HTTP errors with the response", async () => {
    const client = clientWith(async () =>
      Response.json({ detail: "Too many attempts" }, { status: 429, headers: { "Content-Type": "application/json" } }),
    );
    const { data, error, response } = await Auth.login({ client, body: { email: "a@b.c", password: "x" } });
    expect(data).toBeUndefined();
    expect(error).toEqual({ detail: "Too many attempts" });
    expect(response?.status).toBe(429);
  });

  it("returns data on success", async () => {
    const client = clientWith(async () => Response.json({ setup_required: false }));
    const { data } = await Auth.status({ client });
    expect(data).toEqual({ setup_required: false });
  });
});
