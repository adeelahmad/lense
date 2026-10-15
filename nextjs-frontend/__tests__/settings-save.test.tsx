import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom";

import { Admin } from "@/app/openapi-client";
import { SettingsSection } from "@/components/settings/section";
import type { SettingsView } from "@/components/settings/model";
import { TooltipProvider } from "@/components/ui/tooltip";

jest.mock("@/app/openapi-client", () => ({
  Admin: { updateSettings: jest.fn(), testMail: jest.fn() },
  Metadata: {},
}));
jest.mock("next-auth/react", () => ({ useSession: () => ({ data: { accessToken: "t" } }) }));
jest.mock("next/navigation", () => ({ useRouter: () => ({ push: jest.fn() }) }));
jest.mock("@/lib/hooks/session", () => ({ useArchive: () => ({ namespaces: [] }) }));
jest.mock("@/components/ui/toast", () => ({ useToast: () => jest.fn() }));

const ok = (data: unknown) => Promise.resolve({ data, response: { ok: true, status: 200 } });
const VIEW = {
  mail: {
    values: {
      server: "smtp.example.org",
      port: 587,
      username: null,
      password: { set: false },
      from_address: "lens@example.org",
      from_name: "Lens",
      security: "starttls",
    },
  },
} as unknown as SettingsView;

function show() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <TooltipProvider>
        <SettingsSection id="mail" view={VIEW} onErrors={() => undefined} />
      </TooltipProvider>
    </QueryClientProvider>,
  );
}

describe("settings", () => {
  beforeEach(() => (Admin.updateSettings as jest.Mock).mockReset().mockImplementation(() => ok({ ok: true })));

  it("save in one click, without a review", async () => {
    show();
    fireEvent.change(screen.getByLabelText("Port"), { target: { value: "465" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(Admin.updateSettings).toHaveBeenCalledTimes(1));
    expect((Admin.updateSettings as jest.Mock).mock.calls[0][0]).toMatchObject({
      path: { section: "mail" },
      body: { port: 465 },
    });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("save with Ctrl+S", async () => {
    show();
    fireEvent.change(screen.getByLabelText("From name"), { target: { value: "Archive" } });
    fireEvent.keyDown(window, { key: "s", ctrlKey: true });
    await waitFor(() => expect(Admin.updateSettings).toHaveBeenCalledTimes(1));
  });
});

describe("opened on one setting", () => {
  it("focuses its field with the value selected, so typing replaces it", async () => {
    const qc = new QueryClient();
    render(
      <QueryClientProvider client={qc}>
        <TooltipProvider>
          <SettingsSection id="mail" view={VIEW} onErrors={() => undefined} field="mail.port" />
        </TooltipProvider>
      </QueryClientProvider>,
    );
    const port = screen.getByLabelText("Port") as HTMLInputElement;
    await waitFor(() => expect(port).toHaveFocus());
    expect([port.selectionStart, port.selectionEnd]).toEqual([0, port.value.length]);
    expect(port.closest("[data-setting]")).toHaveClass("setting-found");
  });
});

describe("options most people leave alone", () => {
  const UPLOADS = {
    uploads: { values: { max_mb: 2048, chunk_mb: 8, expire_hours: 24, extensions: [".mp3", ".wav"] } },
  } as unknown as SettingsView;
  const uploads = (field?: string) =>
    render(
      <QueryClientProvider client={new QueryClient()}>
        <TooltipProvider>
          <SettingsSection id="uploads" view={UPLOADS} onErrors={() => undefined} field={field} />
        </TooltipProvider>
      </QueryClientProvider>,
    );

  it("are folded under a line saying what's in them, the main one left in view", () => {
    uploads();
    expect(screen.getByLabelText("Largest audio or video file (MB)").closest("details")).toBeNull();
    const fold = screen.getByLabelText("Piece size (MB)").closest("details")!;
    expect(fold).not.toHaveAttribute("open");
    expect(fold.querySelector("summary")).toHaveTextContent("File types (2 of 20)");
  });

  it("open by themselves when one has an error", () => {
    uploads();
    const piece = screen.getByLabelText("Piece size (MB)");
    fireEvent.change(piece, { target: { value: "0" } });
    expect(piece.closest("details")).toHaveAttribute("open");
  });

  it("open on the field Find or ⌘K found", async () => {
    uploads("uploads.expire_hours");
    const hours = screen.getByLabelText("Keep unfinished uploads for (hours)");
    expect(hours.closest("details")).toHaveAttribute("open");
    await waitFor(() => expect(hours).toHaveFocus());
  });
});
