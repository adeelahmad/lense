import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom";

import { Admin } from "@/app/openapi-client";
import { crossErrors, type SettingsView } from "@/components/settings/model";
import { SettingsSection } from "@/components/settings/section";
import { TooltipProvider } from "@/components/ui/tooltip";

jest.mock("@/app/openapi-client", () => ({
  Admin: { updateSettings: jest.fn(), httpsStatus: jest.fn(), httpsProviders: jest.fn(), httpsPorts: jest.fn() },
  Metadata: {},
}));
jest.mock("next-auth/react", () => ({ useSession: () => ({ data: { accessToken: "t" } }) }));
jest.mock("next/navigation", () => ({ useRouter: () => ({ push: jest.fn() }) }));
jest.mock("@/lib/hooks/session", () => ({ useArchive: () => ({ namespaces: [] }) }));
jest.mock("@/components/ui/toast", () => ({ useToast: () => jest.fn() }));

const ok = (data: unknown) => Promise.resolve({ data, response: { ok: true, status: 200 } });
const STATUS = {
  enabled: true,
  running: false,
  ports_open: true,
  url: "https://lens.example.com",
  certificates: [],
  error: null,
  log: [],
  origin: "http://frontend:3000",
  process: "worker-1",
  agent: null,
  manual:
    "curl -fsSL https://raw.githubusercontent.com/adeelahmad/lense/main/install.sh | LENS_REF=main LENS_HTTPS=1 sh",
  reconfigure: null,
};
const PROVIDERS = [
  { name: "cloudflare", label: "Cloudflare", fields: [{ name: "api_token", label: "API token", secret: true }] },
];
const OFF = {
  enabled: false,
  domains: [],
  email: "",
  ca: "both",
  ca_url: "",
  staging: false,
  challenge: "http",
  dns_provider: "",
  dns_package: "",
  dns_credentials: { set: false },
  dns_resolvers: [],
  propagation: "",
  eab_key_id: "",
  eab_hmac_key: { set: false },
  redirect_http: true,
  hsts: false,
  origin: "",
  https_port: 8443,
  http_port: 8080,
};

function show(values: Record<string, unknown>) {
  const view = { https: { values } } as unknown as SettingsView;
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <TooltipProvider>
        <SettingsSection id="https" view={view} onErrors={() => undefined} />
      </TooltipProvider>
    </QueryClientProvider>,
  );
}

describe("Settings › HTTPS", () => {
  beforeEach(() => {
    (Admin.updateSettings as jest.Mock).mockReset().mockImplementation(() => ok({ ok: true }));
    (Admin.httpsStatus as jest.Mock).mockReset();
    (Admin.httpsProviders as jest.Mock).mockReset().mockImplementation(() => ok(PROVIDERS));
    (Admin.httpsPorts as jest.Mock).mockReset();
  });

  it("shows the address and the certificate once it has one", async () => {
    (Admin.httpsStatus as jest.Mock).mockImplementation(() =>
      ok({
        ...STATUS,
        running: true,
        certificates: [{ names: ["lens.example.com"], expires: "2027-01-08T00:00:00+00:00", issuer: "Let's Encrypt" }],
      }),
    );
    show({ ...OFF, enabled: true, domains: ["lens.example.com"] });
    expect(await screen.findByText("Serving over HTTPS.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "https://lens.example.com" })).toBeInTheDocument();
    expect(screen.getByText(/Let's Encrypt · until/)).toBeInTheDocument();
  });

  it("gives the line to run when nothing has the ports and nothing can open them", async () => {
    (Admin.httpsStatus as jest.Mock).mockImplementation(() => ok({ ...STATUS, ports_open: false }));
    show({ ...OFF, enabled: true, domains: ["lens.example.com"] });
    expect(await screen.findByText("No server process has the HTTPS ports yet.")).toBeInTheDocument();
    expect(screen.getByText(/LENS_HTTPS=1 sh/)).toBeInTheDocument();
  });

  it("opens the ports through the upgrade agent", async () => {
    (Admin.httpsStatus as jest.Mock).mockImplementation(() => ok({ ...STATUS, ports_open: false, agent: "systemd" }));
    (Admin.httpsPorts as jest.Mock).mockImplementation(() =>
      ok({
        ...STATUS,
        ports_open: false,
        agent: "systemd",
        reconfigure: { id: "r1", ref: "", https: true, state: "queued", log: [] },
      }),
    );
    show({ ...OFF, enabled: true, domains: ["lens.example.com"] });
    fireEvent.click(await screen.findByRole("button", { name: "Open the HTTPS ports" }));
    expect(await screen.findByText("Opening the HTTPS ports.")).toBeInTheDocument();
    expect((Admin.httpsPorts as jest.Mock).mock.calls[0][0]).toMatchObject({ body: { open: true } });
  });

  it("shows why there's no certificate", async () => {
    (Admin.httpsStatus as jest.Mock).mockImplementation(() =>
      ok({ ...STATUS, running: true, error: "could not get certificate: no valid A records found", log: ["ERROR x"] }),
    );
    show({ ...OFF, enabled: true, domains: ["lens.example.com"] });
    expect(await screen.findByText("could not get certificate: no valid A records found")).toBeInTheDocument();
    expect(screen.getByText(/What Caddy said/)).toBeInTheDocument();
  });

  it("sets up the DNS challenge with Cloudflare", async () => {
    (Admin.httpsStatus as jest.Mock).mockImplementation(() => ok({ ...STATUS, enabled: false }));
    show(OFF);
    expect(await screen.findByText("Off.")).toBeInTheDocument();
    fireEvent.click(screen.getByText("DNS"));
    await screen.findByRole("option", { name: "Cloudflare" });
    fireEvent.change(screen.getByLabelText("DNS provider"), { target: { value: "cloudflare" } });
    fireEvent.change(await screen.findByLabelText("API token"), { target: { value: "cf-secret" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(Admin.updateSettings).toHaveBeenCalledTimes(1));
    expect((Admin.updateSettings as jest.Mock).mock.calls[0][0]).toMatchObject({
      path: { section: "https" },
      body: { challenge: "dns", dns_provider: "cloudflare", dns_credentials: '{"api_token":"cf-secret"}' },
    });
  });

  it("checks the domains, the email and the authority's address", () => {
    expect(crossErrors({ "https.domains": ["not a domain"] })["https.domains"]).toMatch(/domain/);
    expect(crossErrors({ "https.domains": ["*.example.com"], "https.challenge": "http" })["https.domains"]).toMatch(
      /DNS/,
    );
    expect(crossErrors({ "https.domains": ["*.example.com"], "https.challenge": "dns" })).toEqual({});
    expect(crossErrors({ "https.enabled": true, "https.domains": [] })["https.domains"]).toMatch(/domain/);
    expect(crossErrors({ "https.email": "nobody" })["https.email"]).toMatch(/email/);
    expect(crossErrors({ "https.ca": "custom", "https.ca_url": "http://x" })["https.ca_url"]).toMatch(/https/);
  });
});
