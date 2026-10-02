import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import "@testing-library/jest-dom";

import type { Consent, OAuthGrant } from "@/app/openapi-client/types.gen";
import { ConnectedApps } from "@/components/account/apps";
import { ConsentCard, leave } from "@/components/oauth/consent";
import type { ConsentRequest } from "@/components/oauth/model";
import { TooltipProvider } from "@/components/ui/tooltip";

const ok = (data: unknown) => Promise.resolve({ data, response: { ok: true, status: 200 } });
const fail = (status: number, detail: string) =>
  Promise.resolve({ error: { detail }, response: { ok: false, status } });
const api = {
  consent: jest.fn<Promise<unknown>, [unknown]>(),
  answer: jest.fn<Promise<unknown>, [unknown]>(),
  listGrants: jest.fn<Promise<unknown>, [unknown]>(),
  revokeGrant: jest.fn<Promise<unknown>, [unknown]>(),
};
jest.mock("@/app/openapi-client", () => ({
  Oauth: {
    consent: (a: unknown) => api.consent(a),
    answer: (a: unknown) => api.answer(a),
    listGrants: (a: unknown) => api.listGrants(a),
    revokeGrant: (a: unknown) => api.revokeGrant(a),
  },
}));
jest.mock("next-auth/react", () => ({ useSession: () => ({ data: { accessToken: "t" } }) }));
const toast = jest.fn();
jest.mock("@/components/ui/toast", () => ({ useToast: () => toast }));

const request: ConsentRequest = {
  client_id: "lc_1",
  redirect_uri: "https://app.example/cb",
  response_type: "code",
  code_challenge: "c".repeat(43),
  code_challenge_method: "S256",
  scope: "read write",
  state: "s1",
};
const consent = (over: Partial<Consent> = {}): Consent => ({
  client: { id: "lc_1", name: "Harbour Notes", uri: "https://harbour.example" },
  redirect_uri: "https://app.example/cb",
  scope: "read write",
  granted: null,
  ...over,
});

function show(ui: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <TooltipProvider>{ui}</TooltipProvider>
    </QueryClientProvider>,
  );
}

describe("the consent page", () => {
  let go: jest.SpyInstance;
  beforeEach(() => {
    go = jest.spyOn(leave, "to").mockImplementation(() => {});
  });

  it("says which app asks and for what, and Allow sends the browser back with the code", async () => {
    api.consent.mockReturnValue(ok(consent()));
    api.answer.mockReturnValue(ok({ redirect_to: "https://app.example/cb?code=abc&state=s1" }));
    show(<ConsentCard request={request} email="vi@x.io" />);
    expect(await screen.findByRole("heading", { name: "Give Harbour Notes access?" })).toBeInTheDocument();
    expect(screen.getByText("vi@x.io")).toBeInTheDocument();
    expect(screen.getByText("app.example")).toBeInTheDocument();
    expect(screen.getByText(/harbour\.example/)).toBeInTheDocument();
    expect(screen.getByRole("switch", { name: "Let it make changes" })).toBeChecked();
    fireEvent.click(screen.getByRole("button", { name: "Allow" }));
    await waitFor(() => expect(go).toHaveBeenCalledWith("https://app.example/cb?code=abc&state=s1"));
    expect(api.answer).toHaveBeenCalledWith(
      expect.objectContaining({ body: { ...request, approve: true, grant: "read write" } }),
    );
  });

  it("gives read only when changes are switched off, and asks for nothing more when only read was asked", async () => {
    api.consent.mockReturnValue(ok(consent({ granted: "read" })));
    api.answer.mockReturnValue(ok({ redirect_to: "https://app.example/cb?code=abc" }));
    const first = show(<ConsentCard request={request} email="vi@x.io" />);
    expect(await screen.findByText(/You gave this app access before/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("switch", { name: "Let it make changes" }));
    fireEvent.click(screen.getByRole("button", { name: "Allow" }));
    await waitFor(() => expect(go).toHaveBeenCalled());
    expect(api.answer).toHaveBeenCalledWith(
      expect.objectContaining({ body: expect.objectContaining({ grant: "read" }) }),
    );
    first.unmount();

    api.consent.mockReturnValue(ok(consent({ scope: "read" })));
    show(<ConsentCard request={{ ...request, scope: "read" }} email="vi@x.io" />);
    await screen.findByRole("heading", { name: "Give Harbour Notes access?" });
    expect(screen.queryByRole("switch")).not.toBeInTheDocument();
  });

  it("Deny tells the app so", async () => {
    api.consent.mockReturnValue(ok(consent()));
    api.answer.mockReturnValue(ok({ redirect_to: "https://app.example/cb?error=access_denied&state=s1" }));
    show(<ConsentCard request={request} email="vi@x.io" />);
    fireEvent.click(await screen.findByRole("button", { name: "Deny" }));
    await waitFor(() => expect(go).toHaveBeenCalledWith("https://app.example/cb?error=access_denied&state=s1"));
    expect(api.answer).toHaveBeenCalledWith(
      expect.objectContaining({ body: { ...request, approve: false, grant: undefined } }),
    );
  });

  it("sends nobody anywhere for a request the server refuses, an address a browser shouldn't open, or no request", async () => {
    api.consent.mockReturnValue(fail(400, "The address this app wants to return to isn't one it registered."));
    const refused = show(<ConsentCard request={request} email="vi@x.io" />);
    expect(await screen.findByText(/isn't one it registered/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Allow" })).not.toBeInTheDocument();
    refused.unmount();

    api.consent.mockReturnValue(ok(consent()));
    api.answer.mockReturnValue(ok({ redirect_to: "javascript:alert(1)" }));
    const unsafe = show(<ConsentCard request={request} email="vi@x.io" />);
    fireEvent.click(await screen.findByRole("button", { name: "Allow" }));
    expect(await screen.findByText(/can’t be opened from a browser/)).toBeInTheDocument();
    unsafe.unmount();

    api.answer.mockReturnValue(fail(403, "sign in to give an app access; API tokens and apps can't"));
    const failed = show(<ConsentCard request={request} email="vi@x.io" />);
    fireEvent.click(await screen.findByRole("button", { name: "Allow" }));
    expect(await screen.findByText(/sign in to give an app access/)).toBeInTheDocument();
    failed.unmount();

    show(<ConsentCard request={null} email="vi@x.io" />);
    expect(screen.getByRole("heading", { name: "Nothing to give access to" })).toBeInTheDocument();
    expect(go).not.toHaveBeenCalled();
  });
});

describe("apps with access", () => {
  const grant = (over: Partial<OAuthGrant> = {}): OAuthGrant => ({
    id: 3,
    client: "lc_1",
    name: "Harbour Notes",
    uri: "https://harbour.example",
    scope: "read write",
    created_at: "2026-09-30T10:00:00+00:00",
    last_used_at: null,
    expires_at: "2099-01-01T00:00:00+00:00",
    ...over,
  });

  it("lists them and revokes one after asking", async () => {
    api.listGrants.mockReturnValue(ok([grant(), grant({ id: 4, name: "Desk", uri: null, scope: "read" })]));
    api.revokeGrant.mockReturnValue(ok({ ok: true }));
    show(<ConnectedApps />);
    const table = await screen.findByRole("table", { name: "Apps with access" });
    expect(within(table).getByText("Harbour Notes")).toBeInTheDocument();
    expect(within(table).getByText("harbour.example")).toBeInTheDocument();
    expect(within(table).getByText("Read & write")).toBeInTheDocument();
    expect(within(table).getByText("Read only")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Revoke Harbour Notes" }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button", { name: "Revoke access" }));
    await waitFor(() =>
      expect(api.revokeGrant).toHaveBeenCalledWith(expect.objectContaining({ path: { grant_id: 3 } })),
    );
    await waitFor(() =>
      expect(toast).toHaveBeenCalledWith(expect.objectContaining({ title: "Harbour Notes no longer has access" })),
    );
  });

  it("says where assistants connect, to copy", async () => {
    api.listGrants.mockReturnValue(ok([]));
    const writeText = jest.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText } });
    show(<ConnectedApps />);
    expect(await screen.findByText("http://localhost/mcp")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Copy the MCP address" }));
    await waitFor(() => expect(writeText).toHaveBeenCalledWith("http://localhost/mcp"));
    expect(await screen.findByText("Copied")).toBeInTheDocument();
  });

  it("says when there are none, and when they can't be loaded", async () => {
    api.listGrants.mockReturnValue(ok([]));
    const none = show(<ConnectedApps />);
    expect(await screen.findByText("You haven’t given any app access.")).toBeInTheDocument();
    none.unmount();
    api.listGrants.mockReturnValue(fail(500, "boom"));
    show(<ConnectedApps />);
    expect(await screen.findByText("Couldn’t load your apps")).toBeInTheDocument();
  });
});
