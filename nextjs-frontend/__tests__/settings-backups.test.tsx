import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom";

import { Updates } from "@/app/openapi-client";
import { backupStatus } from "@/components/settings/backups-panel";
import { SettingsSection } from "@/components/settings/section";
import type { SettingsView } from "@/components/settings/model";
import type { Backups as BackupList } from "@/app/openapi-client/types.gen";
import { TooltipProvider } from "@/components/ui/tooltip";

jest.mock("@/app/openapi-client", () => ({
  Admin: { updateSettings: jest.fn() },
  Updates: {
    listBackups: jest.fn(),
    takeBackup: jest.fn(),
    restoreBackup: jest.fn(),
    deleteBackup: jest.fn(),
    backupLink: jest.fn(),
  },
  Metadata: {},
}));
jest.mock("next-auth/react", () => ({ useSession: () => ({ data: { accessToken: "t" } }) }));
jest.mock("next/navigation", () => ({ useRouter: () => ({ push: jest.fn() }) }));
jest.mock("@/lib/hooks/session", () => ({ useArchive: () => ({ namespaces: [] }) }));
jest.mock("@/components/ui/toast", () => ({ useToast: () => jest.fn() }));

const ok = (data: unknown) => Promise.resolve({ data, response: { ok: true, status: 200 } });
const VIEW = { backups: { values: { schedule: null, timezone: "UTC", keep: 7 } } } as unknown as SettingsView;
const idle = { running: false, name: null, error: null, finished_at: null, before: null };
const LIST: BackupList = {
  backups: [
    {
      name: "manual-20301001-030000.surql.gz",
      label: "manual",
      kind: "export",
      bytes: 2048,
      taken_at: "2030-10-01T03:00:00Z",
    },
  ],
  in_progress: [],
  restore: idle,
  schedule: { schedule: null, timezone: "UTC", keep: 7 },
  database: "server",
  folder: "/data/backups",
};

function show() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <TooltipProvider>
        <SettingsSection id="backups" view={VIEW} onErrors={() => undefined} />
      </TooltipProvider>
    </QueryClientProvider>,
  );
}

describe("backups settings", () => {
  it("lists the backups and restores one after asking", async () => {
    (Updates.listBackups as jest.Mock).mockImplementation(() => ok(LIST));
    (Updates.restoreBackup as jest.Mock).mockImplementation(() => ok({ ok: true }));
    show();
    expect(await screen.findByText("Taken by hand")).toBeInTheDocument();
    expect(screen.getByText("Back up on a schedule")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Restore the backup/ }));
    expect(screen.getByText("Replace everything with this backup?")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Restore" }));
    await waitFor(() => expect(Updates.restoreBackup).toHaveBeenCalled());
    const call = (Updates.restoreBackup as jest.Mock).mock.calls[0][0];
    expect(call.path).toEqual({ name: LIST.backups[0].name });
    expect(call.body).toEqual({ confirm: LIST.backups[0].name });
  });

  it("says what is running, what failed and what is missing", () => {
    expect(backupStatus({ ...LIST, restore: { ...idle, running: true, name: "x" } }).title).toBe("Restoring x…");
    expect(backupStatus({ ...LIST, in_progress: ["scheduled-1"] }).title).toBe("Backing up…");
    expect(backupStatus({ ...LIST, restore: { ...idle, name: "x", error: "disk full" } }).body).toBe("disk full");
    expect(backupStatus({ ...LIST, backups: [] }).title).toBe("No backups yet.");
    expect(backupStatus({ ...LIST, schedule: { ...LIST.schedule!, last_error: "boom" } }).tone).toBe("error");
  });
});
