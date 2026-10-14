/** @jest-environment node */
import { grafanaAddress, monitoringNote } from "@/components/admin/monitoring-switch";

jest.mock("@/app/openapi-client", () => ({}));
jest.mock("next-auth/react", () => ({ useSession: () => ({ data: null }) }));

const base = { on: true, state: "done" as const, grafana_port: 3001, manual: "docker compose …", agent: "systemd" };

describe("grafanaAddress", () => {
  it("prefers the address set in Settings", () => {
    expect(grafanaAddress("https://grafana.example", base, "lens.local")).toBe("https://grafana.example");
  });
  it("links to the Grafana monitoring runs, on this host, once it is running", () => {
    expect(grafanaAddress(null, base, "lens.local")).toBe("http://lens.local:3001");
    expect(grafanaAddress(null, { ...base, grafana_port: 3005 }, "::1")).toBe("http://[::1]:3005");
    expect(grafanaAddress(null, { ...base, state: "running" }, "lens.local")).toBeNull();
    expect(grafanaAddress(null, { ...base, on: false }, "lens.local")).toBeNull();
    expect(grafanaAddress(undefined, undefined, "lens.local")).toBeNull();
  });
});

describe("monitoringNote", () => {
  it("says what the machine is doing", () => {
    expect(monitoringNote({ ...base, state: "queued" })).toMatch(/Waiting/);
    expect(monitoringNote({ ...base, state: "running" })).toMatch(/Starting/);
    expect(monitoringNote({ ...base, on: false, state: "running" })).toMatch(/Stopping/);
    expect(monitoringNote(base)).toMatch(/Running/);
    expect(monitoringNote({ ...base, on: false, state: null })).toMatch(/^Off/);
  });
});
