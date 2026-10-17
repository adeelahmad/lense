/** @jest-environment node */
import { gatewayNote, sentence } from "@/components/settings/sandbox-gateway";
import { crossErrors, sandboxHost } from "@/components/settings/model";

jest.mock("@/app/openapi-client", () => ({}));
jest.mock("next-auth/react", () => ({ useSession: () => ({ data: null }) }));

const base = {
  on: true,
  state: "done" as const,
  manual: "docker compose …",
  agent: "systemd",
  preflight: [],
  address: "openshell-gateway:17670",
  reachable: true,
};

describe("gatewayNote", () => {
  it("says what the machine is doing, and whether Lens reaches the gateway", () => {
    expect(gatewayNote({ ...base, state: "queued" })).toMatch(/Waiting/);
    expect(gatewayNote({ ...base, state: "running" })).toMatch(/^Starting/);
    expect(gatewayNote({ ...base, on: false, state: "running" })).toMatch(/stopping the gateway/);
    expect(gatewayNote(base)).toBe("Running at openshell-gateway:17670.");
    expect(gatewayNote({ ...base, reachable: false })).toMatch(/can’t reach it/);
    expect(gatewayNote({ ...base, on: false, state: null })).toMatch(/^Off/);
  });
});

describe("sentence", () => {
  it("makes one of the API's reasons a sentence", () => {
    expect(sentence("this machine's Linux kernel has Landlock 2")).toBe("This machine's Linux kernel has Landlock 2.");
    expect(sentence("Done already.")).toBe("Done already.");
  });
});

describe("sandbox hosts", () => {
  it("takes names and wildcards OpenShell's policy takes, never addresses", () => {
    for (const ok of ["api.github.com", "*.wikipedia.org", "**.example.co.uk", "Example.com."])
      expect(sandboxHost(ok)).toBe(true);
    for (const bad of ["localhost", "*.com", "10.0.0.1", "exa mple.com", "-a.example.com", "a*.example.com", ""])
      expect(sandboxHost(bad)).toBe(false);
  });
  it("says which host is wrong", () => {
    expect(crossErrors({ "sandbox.hosts": ["api.github.com", "10.0.0.1"] })["sandbox.hosts"]).toMatch(/10\.0\.0\.1/);
    expect(crossErrors({ "sandbox.hosts": ["api.github.com"] })["sandbox.hosts"]).toBeUndefined();
  });
});
