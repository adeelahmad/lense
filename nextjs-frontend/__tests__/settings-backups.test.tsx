import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom";

import { Sources, Updates } from "@/app/openapi-client";
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
    remoteBackups: jest.fn(),
    sendBackup: jest.fn(),
    fetchBackup: jest.fn(),
  },
  Sources: { listSources: jest.fn(), listBackends: jest.fn() },
  Metadata: {},
}));
jest.mock("next-auth/react", () => ({ useSession: () => ({ data: { accessToken: "t" } }) }));
jest.mock("next/navigation", () => ({ useRouter: () => ({ push: jest.fn() }) }));
jest.mock("@/lib/hooks/session", () => ({ useArchive: () => ({ namespaces: [] }) }));
jest.mock("@/components/ui/toast", () => ({ useToast: () => jest.fn() }));

const ok = (data: unknown) => Promise.resolve({ data, response: { ok: true, status: 200 } });
const VIEW = {
  backups: { values: { schedule: null, timezone: "UTC", keep: 7, connection: null, folder: "lens-backups" } },
} as unknown as SettingsView;
const NO_REMOTE = { connection: null, backups: [], copying: { running: null } };

beforeEach(() => {
  (Sources.listSources as jest.Mock).mockImplementation(() => ok([{ id: 4, name: "s3 box", type: "s3" }]));
  (Sources.listBackends as jest.Mock).mockImplementation(() => ok([]));
  (Updates.remoteBackups as jest.Mock).mockImplementation(() => ok(NO_REMOTE));
});
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
  it("takes a schedule in one click or typed, and offers the storage connections", async () => {
    (Updates.listBackups as jest.Mock).mockImplementation(() => ok(LIST));
    show();
    expect(await screen.findByText("Taken by hand")).toBeInTheDocument();
    const cron = screen.getByLabelText("Back up on a schedule") as HTMLInputElement;
    fireEvent.click(screen.getByRole("button", { name: "Every night at 3:00" }));
    expect(cron.value).toBe("0 3 * * *");
    expect(screen.getByText("Scheduled backups kept")).toBeInTheDocument(); // shown once a schedule is set
    fireEvent.change(cron, { target: { value: "30 2 * * 1-5" } });
    expect(cron.value).toBe("30 2 * * 1-5");
    expect(await screen.findByRole("option", { name: "s3 box (s3)" })).toBeInTheDocument();
  });

  it("brings back copies only on the connection", async () => {
    (Updates.listBackups as jest.Mock).mockImplementation(() => ok(LIST));
    (Updates.fetchBackup as jest.Mock).mockImplementation(() => ok({ ok: true }));
    (Updates.remoteBackups as jest.Mock).mockImplementation(() =>
      ok({
        connection: 4,
        folder: "lens-backups",
        copying: { running: null },
        backups: [
          LIST.backups[0],
          {
            name: "scheduled-20300901-030000.surql.gz",
            label: "scheduled",
            kind: "export",
            bytes: 9,
            taken_at: "2030-09-01T03:00:00Z",
          },
        ],
      }),
    );
    show();
    expect(await screen.findByText("Copied")).toBeInTheDocument();
    fireEvent.click(await screen.findByRole("button", { name: /Bring back the backup/ }));
    await waitFor(() => expect(Updates.fetchBackup).toHaveBeenCalled());
    expect((Updates.fetchBackup as jest.Mock).mock.calls[0][0].path).toEqual({
      name: "scheduled-20300901-030000.surql.gz",
    });
  });

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
