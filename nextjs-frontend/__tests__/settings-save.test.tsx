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
