import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom";

import { Sources } from "@/app/openapi-client";
import type { Source } from "@/app/openapi-client/types.gen";
import { ConnectionDialog } from "@/components/sources/connection-dialog";
import { emptyForm, sourceSubtitle, validateForm } from "@/components/sources/source-model";
import { TooltipProvider } from "@/components/ui/tooltip";
import { navigate } from "@/lib/navigate";

jest.mock("@/app/openapi-client", () => ({
  Sources: {
    createSource: jest.fn(),
    updateSource: jest.fn(),
    testSource: jest.fn(),
    deleteSource: jest.fn(),
    listSources: jest.fn(),
    googleClient: jest.fn(),
    connectGoogle: jest.fn(),
    startRcloneSignin: jest.fn(),
    finishRcloneSignin: jest.fn(),
    cancelRcloneSignin: jest.fn(),
  },
}));
jest.mock("next-auth/react", () => ({ useSession: () => ({ data: { accessToken: "t" } }) }));
jest.mock("@/components/ui/toast", () => ({ useToast: () => jest.fn() }));
jest.mock("@/lib/navigate", () => ({ navigate: { assign: jest.fn(), replace: jest.fn() } }));

const ok = (data: unknown) => Promise.resolve({ data, response: { ok: true, status: 200 } });
const DRIVE = {
  label: "Google Drive",
  fields: { scope: "drive.readonly", root_folder_id: "", team_drive: "", impersonate: "", client_id: "", account: "" },
  secrets: ["token", "service_account_credentials", "client_secret"],
  oauth: "rclone authorize drive",
};
const CALLBACK = "https://lens.example/api/v1/auth/external/google/callback";
const CONSENT = "https://accounts.google.com/o/oauth2/v2/auth?client_id=c";
const FOLDER = "https://drive.google.com/drive/folders/1AbC";

function show(source?: Source) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <TooltipProvider>
        <ConnectionDialog open onOpenChange={() => undefined} backends={{ drive: DRIVE }} source={source} />
      </TooltipProvider>
    </QueryClientProvider>,
  );
  if (!source) fireEvent.click(screen.getByRole("radio", { name: /Google Drive/ }));
}

describe("Google Drive with Connect with Google", () => {
  beforeEach(() => {
    (Sources.createSource as jest.Mock)
      .mockReset()
      .mockImplementation(() => ok({ id: 7, health: { ok: false, error: "empty token" } }));
    (Sources.updateSource as jest.Mock).mockReset().mockImplementation(() => ok({ ok: true, health: { ok: true } }));
    (Sources.connectGoogle as jest.Mock).mockReset().mockImplementation(() => ok({ url: CONSENT }));
    (navigate.assign as jest.Mock).mockReset();
  });

  it("connects through the Google client Lens has, in one click", async () => {
    (Sources.googleClient as jest.Mock).mockImplementation(() =>
      ok({ origin: "sign-in", client_id: "c", redirect_uri: CALLBACK }),
    );
    show();
    const connect = await screen.findByRole("button", { name: "Connect with Google" });
    expect(screen.getByText(/Through Google sign-in’s OAuth client/)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText(/Root folder/), { target: { value: FOLDER } });
    fireEvent.click(connect);
    await waitFor(() => expect(navigate.assign).toHaveBeenCalledWith(CONSENT));
    expect((Sources.createSource as jest.Mock).mock.calls[0][0].body).toEqual({
      type: "drive",
      name: "Google Drive",
      params: {
        scope: "drive.readonly",
        root_folder_id: FOLDER,
        team_drive: "",
        impersonate: "",
        client_id: "",
        account: "",
      },
      secrets: {},
    });
    expect((Sources.connectGoogle as jest.Mock).mock.calls[0][0]).toMatchObject({ path: { sid: 7 }, body: {} });
    // rclone is still a click away
    expect(screen.getByRole("button", { name: /sign in through rclone/ })).toBeInTheDocument();
  });

  it("signs in through rclone when Lens has no Google client, with your own app a click away", async () => {
    (Sources.googleClient as jest.Mock).mockImplementation(() =>
      ok({ origin: null, client_id: null, redirect_uri: CALLBACK }),
    );
    show();
    expect(await screen.findByRole("button", { name: "Sign in with Google" })).toBeInTheDocument(); // rclone's
    fireEvent.click(await screen.findByRole("button", { name: "Connect through your own Google app instead" }));
    expect(screen.getByLabelText("OAuth client ID")).toBeInTheDocument();
    expect(screen.getByText(/enable the Google Drive API/)).toBeInTheDocument();
    expect(screen.getByText(CALLBACK)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("OAuth client ID"), { target: { value: "123.apps.googleusercontent.com" } });
    fireEvent.change(screen.getByLabelText("Client secret"), { target: { value: "shh" } });
    fireEvent.click(screen.getByRole("button", { name: "Connect with Google" }));
    await waitFor(() => expect(navigate.assign).toHaveBeenCalledWith(CONSENT));
    expect((Sources.connectGoogle as jest.Mock).mock.calls[0][0].body).toEqual({
      client_id: "123.apps.googleusercontent.com",
      client_secret: "shh",
    });
  });

  it("shows the connected account, and connects again from there", async () => {
    (Sources.googleClient as jest.Mock).mockImplementation(() =>
      ok({ origin: "connection", client_id: "c", redirect_uri: CALLBACK }),
    );
    const set = { secret: true, set: true };
    show({
      id: 7,
      name: "Mara's Drive",
      type: "drive",
      params: { ...DRIVE.fields, client_id: "c", account: "mara@harbour.example" },
      secrets: { token: set, client_secret: set, service_account_credentials: { secret: true, set: false } },
      health: { ok: true },
    } as unknown as Source);
    expect(await screen.findByText("mara@harbour.example")).toBeInTheDocument();
    const again = screen.getByRole("button", { name: "Connect again" });
    await waitFor(() => expect(again).toBeEnabled()); // once Lens knows its Google client
    fireEvent.click(again);
    await waitFor(() => expect(navigate.assign).toHaveBeenCalledWith(CONSENT));
    expect(Sources.createSource).not.toHaveBeenCalled();
  });

  it("knows which way a Drive connection signed in", () => {
    const set = { secret: true, set: true };
    const google = { name: "d", params: { client_id: "c", account: "mara@harbour.example" }, secrets: { token: set } };
    expect(emptyForm(DRIVE, google).signInWith).toBe("google");
    expect(emptyForm(DRIVE, { ...google, params: {} }).signInWith).toBe("rclone");
    expect(validateForm("drive", DRIVE, { ...emptyForm(DRIVE), signInWith: "google" }, false)).toEqual({
      token: "Connect a Google account first.",
    });
    expect(sourceSubtitle({ type: "drive", params: { ...google.params, root_folder_id: "1AbC" } })).toBe(
      "Drive · as mara@harbour.example · read only · one folder",
    );
  });
});
