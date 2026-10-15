import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom";

import { Auth, Sources } from "@/app/openapi-client";
import { ConnectionDialog } from "@/components/sources/connection-dialog";
import { TooltipProvider } from "@/components/ui/tooltip";
import { navigate } from "@/lib/navigate";

jest.mock("@/app/openapi-client", () => ({
  Sources: {
    createSource: jest.fn(),
    updateSource: jest.fn(),
    testSource: jest.fn(),
    deleteSource: jest.fn(),
    startSignin: jest.fn(),
    listSources: jest.fn(),
  },
  Auth: { addLoginProvider: jest.fn() },
}));
jest.mock("next-auth/react", () => ({ useSession: () => ({ data: { accessToken: "t" } }) }));
jest.mock("@/components/ui/toast", () => ({ useToast: () => jest.fn() }));
jest.mock("@/lib/navigate", () => ({ navigate: { assign: jest.fn(), replace: jest.fn() } }));

/** The same-site channel the /connected page answers on, kept in this test's memory. */
class Channel {
  static open: Channel[] = [];
  onmessage: ((e: { data: unknown }) => void) | null = null;
  constructor(public name: string) {
    Channel.open.push(this);
  }
  postMessage(data: unknown) {
    for (const c of Channel.open) if (c !== this && c.name === this.name) c.onmessage?.({ data });
  }
  close() {
    Channel.open = Channel.open.filter((c) => c !== this);
  }
}

const ok = (data: unknown) => Promise.resolve({ data, response: { ok: true, status: 200 } });
const SIGNIN = { kind: "google", label: "Google", ready: true, callback_path: "/api/v1/auth/external/google/callback" };
const DRIVE = {
  label: "Google Drive",
  fields: { scope: "drive.readonly", root_folder_id: "", team_drive: "", impersonate: "" },
  secrets: ["token", "service_account_credentials"],
  oauth: "rclone authorize drive",
};

function show(onSaved: (id: number) => void, signin: typeof SIGNIN | null = SIGNIN) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <TooltipProvider>
        <ConnectionDialog
          open
          onOpenChange={() => undefined}
          backends={{ drive: { ...DRIVE, signin } }}
          onSaved={onSaved}
        />
      </TooltipProvider>
    </QueryClientProvider>,
  );
}

describe("connecting Google Drive by signing in with Google", () => {
  const popup = { location: { href: "" }, close: jest.fn() };
  beforeEach(() => {
    Channel.open = [];
    (globalThis as { BroadcastChannel?: unknown }).BroadcastChannel = Channel;
    jest.spyOn(window, "open").mockReturnValue(popup as unknown as Window);
    (Sources.startSignin as jest.Mock)
      .mockReset()
      .mockImplementation(() => ok({ url: "https://google.test/auth", bound: true }));
    (Sources.listSources as jest.Mock).mockReset().mockImplementation(() =>
      ok([
        {
          id: 7,
          name: "Google Drive · ada@gmail.com",
          type: "drive",
          params: {},
          secrets: {},
          signed_in: { provider: "Google", email: "ada@gmail.com" },
          health: { ok: true },
        },
      ]),
    );
    (Sources.updateSource as jest.Mock).mockReset().mockImplementation(() => ok({ health: { ok: true } }));
  });

  it("signs in in a small window, then tests and names the connection it made", async () => {
    const saved = jest.fn();
    show(saved);
    fireEvent.click(screen.getByRole("radio", { name: /Google Drive/ }));
    fireEvent.change(screen.getByLabelText(/Root folder ID/), { target: { value: "1AbC" } });
    expect(screen.queryByRole("button", { name: "Test connection" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Sign in with Google" }));
    await waitFor(() => expect(popup.location.href).toBe("https://google.test/auth"));
    const body = (Sources.startSignin as jest.Mock).mock.calls[0][0].body;
    expect(body).toMatchObject({
      type: "drive",
      name: null,
      params: { root_folder_id: "1AbC", scope: "drive.readonly" },
    });
    expect(screen.getByRole("button", { name: "Waiting for Google…" })).toHaveAttribute("aria-disabled", "true");
    // the /connected page, in the window, says it worked
    const tag = new URLSearchParams(body.next.split("?")[1]).get("tag");
    act(() => new Channel("lens-connect").postMessage({ tag, connected: 7 }));
    expect(await screen.findByLabelText("Name")).toHaveValue("Google Drive · ada@gmail.com");
    expect(screen.getByText("Listed the top folder")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Done" }));
    await waitFor(() => expect(saved).toHaveBeenCalledWith(7));
    expect(Sources.createSource).not.toHaveBeenCalled();
    expect(Sources.updateSource).not.toHaveBeenCalled(); // the name it was made with was kept
  });

  it("goes with the whole page where popups are blocked, and shows why it didn't work", async () => {
    (window.open as jest.Mock).mockReturnValue(null);
    show(jest.fn());
    fireEvent.click(screen.getByRole("radio", { name: /Google Drive/ }));
    fireEvent.click(screen.getByRole("button", { name: "Sign in with Google" }));
    await waitFor(() => expect(navigate.assign).toHaveBeenCalledWith("https://google.test/auth"));
    expect((Sources.startSignin as jest.Mock).mock.calls[0][0].body.next).toBe("/");
  });

  it("asks for the Google app first, and keeps signing in to Lens with Google off", async () => {
    (Auth.addLoginProvider as jest.Mock).mockReset().mockImplementation(() => ok({ key: "google" }));
    show(jest.fn(), { ...SIGNIN, ready: false });
    fireEvent.click(screen.getByRole("radio", { name: /Google Drive/ }));
    expect(screen.getByText(/http:\/\/localhost\/api\/v1\/auth\/external\/google\/callback/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Sign in with Google" })).toHaveAttribute("aria-disabled", "true");
    fireEvent.change(screen.getByLabelText("Client ID"), { target: { value: "123.apps.googleusercontent.com" } });
    fireEvent.change(screen.getByLabelText("Client secret"), { target: { value: "GOCSPX-x" } });
    fireEvent.click(screen.getByRole("button", { name: "Save app" }));
    await waitFor(() =>
      expect((Auth.addLoginProvider as jest.Mock).mock.calls[0][0].body).toEqual({
        kind: "google",
        client_id: "123.apps.googleusercontent.com",
        client_secret: "GOCSPX-x",
        enabled: false,
      }),
    );
    // or the token rclone printed, as before
    fireEvent.click(screen.getByRole("button", { name: "Or paste a token from rclone" }));
    expect(screen.getByText(/Run this on a computer with a browser/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Test connection" })).toBeInTheDocument();
  });
});
