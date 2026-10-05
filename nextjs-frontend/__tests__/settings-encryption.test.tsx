import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import "@testing-library/jest-dom";

import { Admin } from "@/app/openapi-client";
import { SettingsSection } from "@/components/settings/section";
import type { SettingsView } from "@/components/settings/model";
import { TooltipProvider } from "@/components/ui/tooltip";

jest.mock("@/app/openapi-client", () => ({
  Admin: { updateSettings: jest.fn(), getEncryption: jest.fn() },
  Metadata: {},
}));
jest.mock("next-auth/react", () => ({ useSession: () => ({ data: { accessToken: "t" } }) }));
jest.mock("next/navigation", () => ({ useRouter: () => ({ push: jest.fn() }) }));
jest.mock("@/lib/hooks/session", () => ({ useArchive: () => ({ namespaces: [] }) }));
jest.mock("@/components/ui/toast", () => ({ useToast: () => jest.fn() }));

const ok = (data: unknown) => Promise.resolve({ data, response: { ok: true, status: 200 } });
const VIEW = {
  encryption: { values: { files: true, work_minutes: 30, vault_minutes: 60 } },
} as unknown as SettingsView;

function show() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <TooltipProvider>
        <SettingsSection id="encryption" view={VIEW} onErrors={() => undefined} />
      </TooltipProvider>
    </QueryClientProvider>,
  );
}

describe("encryption settings", () => {
  it("says files are encrypted when nothing is being converted", async () => {
    (Admin.getEncryption as jest.Mock).mockImplementation(() =>
      ok({ running: false, to: null, changed: 0, skipped: 0 }),
    );
    show();
    expect(await screen.findByText("Files are encrypted on disk.")).toBeInTheDocument();
    expect(screen.getByText("Encrypt files on disk")).toBeInTheDocument();
  });

  it("shows how far converting the files already kept has got", async () => {
    (Admin.getEncryption as jest.Mock).mockImplementation(() =>
      ok({ running: true, to: "encrypted", changed: 12, skipped: 0 }),
    );
    show();
    expect(await screen.findByText("Encrypting the files already kept…")).toBeInTheDocument();
    expect(screen.getByText(/12 done so far/)).toBeInTheDocument();
  });

  it("names the files it skipped", async () => {
    (Admin.getEncryption as jest.Mock).mockImplementation(() =>
      ok({ running: false, to: "plain", changed: 3, skipped: 2, finished_at: 1 }),
    );
    show();
    expect(await screen.findByText("3 file(s) turned back to plain.")).toBeInTheDocument();
    expect(screen.getByText(/2 skipped/)).toBeInTheDocument();
  });
});
