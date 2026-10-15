import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom";

import { Sources } from "@/app/openapi-client";
import { ConnectionDialog } from "@/components/sources/connection-dialog";
import { isRcloneReturn } from "@/components/sources/rclone-signin";
import { TooltipProvider } from "@/components/ui/tooltip";

jest.mock("@/app/openapi-client", () => ({
  Sources: {
    createSource: jest.fn(),
    updateSource: jest.fn(),
    testSource: jest.fn(),
    deleteSource: jest.fn(),
    listSources: jest.fn(),
    startRcloneSignin: jest.fn(),
    finishRcloneSignin: jest.fn(),
    cancelRcloneSignin: jest.fn(),
  },
}));
jest.mock("next-auth/react", () => ({ useSession: () => ({ data: { accessToken: "t" } }) }));
jest.mock("@/components/ui/toast", () => ({ useToast: () => jest.fn() }));

const ok = (data: unknown) => Promise.resolve({ data, response: { ok: true, status: 200 } });
const fail = (status: number, detail: string) =>
  Promise.resolve({ error: { detail }, response: { ok: false, status } });
const DROPBOX = { label: "Dropbox", fields: {}, secrets: ["token"], oauth: "rclone authorize dropbox" };
const BACK = "http://127.0.0.1:53682/?state=s1&code=c1";

function show(onSaved: (id: number) => void = jest.fn()) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <TooltipProvider>
        <ConnectionDialog open onOpenChange={() => undefined} backends={{ dropbox: DROPBOX }} onSaved={onSaved} />
      </TooltipProvider>
    </QueryClientProvider>,
  );
  fireEvent.click(screen.getByRole("radio", { name: /Dropbox/ }));
}

describe("signing in through rclone on the server", () => {
  const tab = { location: { href: "" }, opener: {} as unknown, close: jest.fn() };
  beforeEach(() => {
    tab.location.href = "";
    tab.opener = {};
    jest.spyOn(window, "open").mockReturnValue(tab as unknown as Window);
    (Sources.startRcloneSignin as jest.Mock)
      .mockReset()
      .mockImplementation(() =>
        ok({ id: "f1", url: "https://dropbox.test/authorize", port: 53682, expires_at: "2030-01-01T00:00:00+00:00" }),
      );
    (Sources.finishRcloneSignin as jest.Mock).mockReset().mockImplementation(() => ok({ connected: null }));
    (Sources.cancelRcloneSignin as jest.Mock).mockReset().mockImplementation(() => ok({ ok: true }));
    (Sources.listSources as jest.Mock)
      .mockReset()
      .mockImplementation(() =>
        ok([{ id: 9, name: "Dropbox", type: "dropbox", params: {}, secrets: {}, health: { ok: true } }]),
      );
  });

  it("opens the service in a new tab, takes the address it ends on, then tests and names the connection", async () => {
    const saved = jest.fn();
    show(saved);
    expect(screen.queryByRole("button", { name: "Test connection" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Sign in with Dropbox" }));
    await waitFor(() => expect(tab.location.href).toBe("https://dropbox.test/authorize"));
    expect(window.open).toHaveBeenCalledWith("", "_blank");
    expect(tab.opener).toBeNull();
    expect((Sources.startRcloneSignin as jest.Mock).mock.calls[0][0].body).toEqual({
      type: "dropbox",
      params: {},
      name: null,
    });
    const paste = screen.getByLabelText(/Paste the address it ends on/);
    expect(paste).toHaveFocus();
    // the address goes as soon as it's pasted
    (Sources.finishRcloneSignin as jest.Mock).mockImplementation(() => ok({ connected: 9 }));
    fireEvent.change(paste, { target: { value: BACK } });
    await waitFor(() =>
      expect(Sources.finishRcloneSignin).toHaveBeenCalledWith(
        expect.objectContaining({ path: { fid: "f1" }, body: { address: BACK } }),
      ),
    );
    expect(await screen.findByLabelText("Name")).toHaveValue("Dropbox");
    expect(screen.getByText("Listed the top folder")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Done" }));
    await waitFor(() => expect(saved).toHaveBeenCalledWith(9));
    expect(Sources.createSource).not.toHaveBeenCalled();
  });

  it("keeps waiting after a wrong address, and starts over when the sign-in ended", async () => {
    show();
    fireEvent.click(screen.getByRole("button", { name: "Sign in with Dropbox" }));
    const paste = await screen.findByLabelText(/Paste the address it ends on/);
    (Sources.finishRcloneSignin as jest.Mock).mockImplementation(() =>
      fail(400, "that address is from another sign-in; paste the one this sign-in ended on"),
    );
    fireEvent.change(paste, { target: { value: "http://127.0.0.1:53682/?state=old&code=c0" } });
    expect(await screen.findByText(/that address is from another sign-in/)).toBeInTheDocument();
    expect(screen.getByLabelText(/Paste the address it ends on/)).toBeInTheDocument();
    (Sources.finishRcloneSignin as jest.Mock).mockImplementation(() =>
      fail(410, "signing in didn't finish: you cancelled"),
    );
    fireEvent.change(paste, { target: { value: BACK.replace("code=c1", "error=access_denied") } });
    expect(await screen.findByText("signing in didn't finish: you cancelled")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Sign in with Dropbox" })).toBeInTheDocument();
  });

  it("finishes by itself when the browser reached rclone, and cancelling stops it on the server", async () => {
    jest.useFakeTimers();
    try {
      show();
      fireEvent.click(screen.getByRole("button", { name: "Sign in with Dropbox" }));
      await screen.findByLabelText(/Paste the address it ends on/);
      fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
      expect(Sources.cancelRcloneSignin).toHaveBeenCalledWith(expect.objectContaining({ path: { fid: "f1" } }));
      fireEvent.click(screen.getByRole("button", { name: "Sign in with Dropbox" }));
      await screen.findByLabelText(/Paste the address it ends on/);
      (Sources.finishRcloneSignin as jest.Mock).mockImplementation(() => ok({ connected: 9 }));
      await act(async () => {
        jest.advanceTimersByTime(2100);
      });
      expect(Sources.finishRcloneSignin).toHaveBeenCalledWith(expect.objectContaining({ body: {} }));
      expect(await screen.findByLabelText("Name")).toHaveValue("Dropbox");
    } finally {
      jest.useRealTimers();
    }
  });

  it("knows the address the service sends the browser back to", () => {
    expect(isRcloneReturn(BACK, 53682)).toBe(true);
    expect(isRcloneReturn("http://localhost:53682/?code=x&state=y", 53682)).toBe(true); // OneDrive's
    expect(isRcloneReturn("http://127.0.0.1:53682/?error=access_denied", 53682)).toBe(true);
    expect(isRcloneReturn("http://127.0.0.1:53682/", 53682)).toBe(false);
    expect(isRcloneReturn("https://evil.test/?code=x", 53682)).toBe(false);
    expect(isRcloneReturn("not a link", 53682)).toBe(false);
  });
});
