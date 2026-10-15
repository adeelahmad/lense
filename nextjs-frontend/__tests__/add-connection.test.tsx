import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom";

import { Sources } from "@/app/openapi-client";
import { ConnectionDialog } from "@/components/sources/connection-dialog";
import { TooltipProvider } from "@/components/ui/tooltip";
import { navigate } from "@/lib/navigate";

jest.mock("@/app/openapi-client", () => ({
  Sources: {
    createSource: jest.fn(),
    updateSource: jest.fn(),
    testSource: jest.fn(),
    deleteSource: jest.fn(),
    googleClient: jest.fn(),
    connectGoogle: jest.fn(),
  },
}));
jest.mock("next-auth/react", () => ({ useSession: () => ({ data: { accessToken: "t" } }) }));
jest.mock("@/components/ui/toast", () => ({ useToast: () => jest.fn() }));
jest.mock("@/lib/navigate", () => ({ navigate: { assign: jest.fn(), replace: jest.fn() } }));

const ok = (data: unknown) => Promise.resolve({ data, response: { ok: true, status: 200 } });
const BACKENDS = {
  local: { label: "This machine", fields: {}, secrets: [] },
  smb: { label: "SMB", fields: { host: "", user: "", port: "", domain: "" }, secrets: ["pass"] },
  gcal: {
    label: "Google Calendar",
    fields: { calendars: "", impersonate: "", days_back: "365", account: "" },
    secrets: ["token", "service_account_credentials"],
  },
};
const CALLBACK = "https://lens.example/api/v1/auth/external/google/callback";
const CONSENT = "https://accounts.google.com/o/oauth2/v2/auth?client_id=c";

function show(onSaved: (id: number) => void) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <TooltipProvider>
        <ConnectionDialog open onOpenChange={() => undefined} backends={BACKENDS} onSaved={onSaved} />
      </TooltipProvider>
    </QueryClientProvider>,
  );
}

describe("adding a connection", () => {
  beforeEach(() => {
    (Sources.createSource as jest.Mock).mockReset().mockImplementation(() => ok({ id: 7, health: { ok: true } }));
    (Sources.updateSource as jest.Mock).mockReset().mockImplementation(() => ok({ id: 7, health: { ok: true } }));
  });

  it("adds a folder on this machine at once: nothing to fill in, test or name", async () => {
    const saved = jest.fn();
    show(saved);
    fireEvent.click(screen.getByRole("radio", { name: /This machine/ }));
    await waitFor(() => expect(saved).toHaveBeenCalledWith(7));
    expect((Sources.createSource as jest.Mock).mock.calls[0][0].body).toEqual({
      type: "local",
      name: "Folder on this machine",
      params: {},
      secrets: {},
    });
    expect(Sources.updateSource).not.toHaveBeenCalled();
  });

  it("shows the test step for a folder on this machine only when its test fails", async () => {
    (Sources.createSource as jest.Mock).mockImplementation(() =>
      ok({ id: 7, health: { ok: false, error: "no sources.local_roots are configured" } }),
    );
    const saved = jest.fn();
    show(saved);
    fireEvent.click(screen.getByRole("radio", { name: /This machine/ }));
    expect(await screen.findByText("no sources.local_roots are configured")).toBeInTheDocument();
    expect(screen.getByLabelText("Name")).toHaveValue("Folder on this machine");
    expect(screen.getByRole("button", { name: "Discard" })).toBeInTheDocument();
    expect(saved).not.toHaveBeenCalled();
  });

  it("goes type → details → tested and named, with no extra steps", async () => {
    const saved = jest.fn();
    show(saved);
    fireEvent.click(screen.getByRole("radio", { name: /SMB/ })); // one click on the type moves on
    fireEvent.change(screen.getByLabelText("Host"), { target: { value: "nas.lan" } });
    fireEvent.click(screen.getByRole("button", { name: "Test connection" }));
    const name = await screen.findByLabelText("Name");
    expect(name).toHaveValue("SMB · nas.lan");
    expect((Sources.createSource as jest.Mock).mock.calls[0][0].body).toMatchObject({
      type: "smb",
      name: "SMB · nas.lan",
      params: { host: "nas.lan" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Done" }));
    await waitFor(() => expect(saved).toHaveBeenCalledWith(7));
    expect(Sources.updateSource).not.toHaveBeenCalled(); // the suggested name was kept: nothing to save again
  });

  it("saves a name changed on the test screen", async () => {
    const saved = jest.fn();
    show(saved);
    fireEvent.click(screen.getByRole("radio", { name: /SMB/ }));
    fireEvent.change(screen.getByLabelText("Host"), { target: { value: "nas.lan" } });
    fireEvent.click(screen.getByRole("button", { name: "Test connection" }));
    fireEvent.change(await screen.findByLabelText("Name"), { target: { value: "Scans" } });
    fireEvent.click(screen.getByRole("button", { name: "Done" }));
    await waitFor(() => expect(saved).toHaveBeenCalledWith(7));
    expect((Sources.updateSource as jest.Mock).mock.calls[0][0]).toMatchObject({
      path: { sid: 7 },
      body: { name: "Scans" },
    });
  });
});

describe("connecting a Google account", () => {
  beforeEach(() => {
    (Sources.createSource as jest.Mock)
      .mockReset()
      .mockImplementation(() =>
        ok({ id: 7, health: { ok: false, error: "connect a Google account, or upload a service account key, first" } }),
      );
    (Sources.connectGoogle as jest.Mock).mockReset().mockImplementation(() => ok({ url: CONSENT }));
    (navigate.assign as jest.Mock).mockReset();
  });

  it("saves the connection and goes to Google's consent screen in one click", async () => {
    (Sources.googleClient as jest.Mock).mockImplementation(() =>
      ok({ origin: "sign-in", client_id: "c", redirect_uri: CALLBACK }),
    );
    show(jest.fn());
    fireEvent.click(screen.getByRole("radio", { name: /Google Calendar/ }));
    expect(await screen.findByText(/Through Google sign-in’s OAuth client/)).toBeInTheDocument();
    expect(screen.queryByLabelText("OAuth client ID")).toBeNull(); // nothing to fill in: Lens has a client
    fireEvent.change(screen.getByLabelText(/Calendars/), { target: { value: "team@group.calendar.google.com" } });
    fireEvent.click(screen.getByRole("button", { name: "Connect with Google" }));
    await waitFor(() => expect(navigate.assign).toHaveBeenCalledWith(CONSENT));
    expect((Sources.createSource as jest.Mock).mock.calls[0][0].body).toEqual({
      type: "gcal",
      name: "Google Calendar",
      params: { calendars: "team@group.calendar.google.com", impersonate: "", days_back: "365", account: "" },
      secrets: {},
    });
    expect((Sources.connectGoogle as jest.Mock).mock.calls[0][0]).toMatchObject({ path: { sid: 7 }, body: {} });
  });

  it("asks for an OAuth client, and shows the redirect URI to register, only when Lens has none", async () => {
    (Sources.googleClient as jest.Mock).mockImplementation(() =>
      ok({ origin: null, client_id: null, redirect_uri: CALLBACK }),
    );
    show(jest.fn());
    fireEvent.click(screen.getByRole("radio", { name: /Google Calendar/ }));
    const id = await screen.findByLabelText("OAuth client ID");
    expect(screen.getByText(CALLBACK)).toBeInTheDocument();
    const connect = screen.getByRole("button", { name: "Connect with Google" });
    expect(connect).toBeDisabled();
    fireEvent.change(id, { target: { value: " 123.apps.googleusercontent.com " } });
    fireEvent.change(screen.getByLabelText("Client secret"), { target: { value: "shh" } });
    fireEvent.click(connect);
    await waitFor(() => expect(navigate.assign).toHaveBeenCalledWith(CONSENT));
    expect((Sources.connectGoogle as jest.Mock).mock.calls[0][0].body).toEqual({
      client_id: "123.apps.googleusercontent.com",
      client_secret: "shh",
    });
  });
});
