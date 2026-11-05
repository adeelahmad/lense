import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom";

import { Admin } from "@/app/openapi-client";
import { fieldId, fieldsOf, toUi, type SectionId, type SettingsView } from "@/components/settings/model";
import { SettingsSection } from "@/components/settings/section";
import { TooltipProvider } from "@/components/ui/tooltip";

jest.mock("@/app/openapi-client", () => ({
  Admin: {
    updateSettings: jest.fn(),
    getSecrets: jest.fn(),
    telegramStatus: jest.fn(),
    slackStatus: jest.fn(),
    mailboxStatus: jest.fn(),
  },
  Metadata: {},
}));
jest.mock("next-auth/react", () => ({ useSession: () => ({ data: { accessToken: "t" } }) }));
jest.mock("next/navigation", () => ({ useRouter: () => ({ push: jest.fn() }) }));
jest.mock("@/lib/hooks/session", () => ({ useArchive: () => ({ namespaces: [] }) }));
jest.mock("@/components/ui/toast", () => ({ useToast: () => jest.fn() }));

const ok = (data: unknown) => Promise.resolve({ data, response: { ok: true, status: 200 } });
const CREDENTIALS: [SectionId, string, string][] = [
  ["telegram", "token", "Bot token"],
  ["slack", "bot_token", "Bot token (xoxb-…, from OAuth & Permissions)"],
  ["slack", "app_token", "App-level token (xapp-…, from Basic Information, with connections:write)"],
  ["mailbox", "password", "Password"],
];

function view(section: SectionId, key: string, saved: boolean): SettingsView {
  return {
    [section]: {
      values: {
        enabled: false,
        account: null,
        name: "Lens",
        answer: "mention",
        users: [],
        rooms: [],
        approve: "low_risk",
        api_url: null,
        token: { secret: true, set: false },
        bot_token: { secret: true, set: false },
        app_token: { secret: true, set: false },
        address: null,
        password: { secret: true, set: false },
        username: null,
        imap_host: null,
        imap_port: null,
        imap_security: "ssl",
        smtp_host: null,
        smtp_port: null,
        smtp_security: "starttls",
        folder: "INBOX",
        routes: [],
        imports: "auto",
        check: "authenticated",
        auth_server: null,
        poll_seconds: 30,
        [key]: { secret: true, set: saved },
      },
    },
  } as SettingsView;
}

function show(section: SectionId, key: string, saved = false) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <TooltipProvider>
        <SettingsSection id={section} view={view(section, key, saved)} onErrors={() => undefined} />
      </TooltipProvider>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  (Admin.updateSettings as jest.Mock).mockReset().mockImplementation(() => ok({ ok: true }));
  (Admin.getSecrets as jest.Mock).mockReset().mockImplementation(() => ok({ providers: [], references: [], fields: [] }));
  for (const status of [Admin.telegramStatus, Admin.slackStatus, Admin.mailboxStatus])
    (status as jest.Mock).mockReset().mockImplementation(() => ok({ state: "off", people: [], chats: [] }));
});

describe.each(CREDENTIALS)("%s.%s", (section, key, label) => {
  it("registers the custom credential control as a write-only field", () => {
    const field = fieldsOf(section).find((f) => fieldId(f) === `${section}.${key}`);
    expect(field).toMatchObject({ section, key, kind: "secret" });
    expect(toUi(field!, { secret: true, set: true })).toBeUndefined();
  });

  it("opens without crashing and saves a new credential", async () => {
    show(section, key);
    const input = screen.getByLabelText(label);
    expect(input).toHaveAttribute("type", "password");
    expect(input).toHaveValue("");
    expect(screen.queryByRole("button", { name: "Save" })).not.toBeInTheDocument();
    fireEvent.change(input, { target: { value: "test-credential" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(Admin.updateSettings).toHaveBeenCalledTimes(1));
    expect((Admin.updateSettings as jest.Mock).mock.calls[0][0]).toMatchObject({
      path: { section },
      body: { [key]: "test-credential" },
    });
    expect((Admin.updateSettings as jest.Mock).mock.calls[0][0].body).toEqual({ [key]: "test-credential" });
  });

  it("keeps a saved credential unchanged after Cancel and Undo", () => {
    show(section, key, true);
    fireEvent.click(screen.getByRole("button", { name: "Replace" }));
    fireEvent.change(screen.getByLabelText(label), { target: { value: "replacement" } });
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("button", { name: "Save" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Clear" }));
    fireEvent.click(screen.getByRole("button", { name: "Undo" }));
    expect(screen.queryByRole("button", { name: "Save" })).not.toBeInTheDocument();
    expect(Admin.updateSettings).not.toHaveBeenCalled();
  });

  it("submits an explicit clear without resubmitting masked secrets", async () => {
    show(section, key, true);
    fireEvent.click(screen.getByRole("button", { name: "Clear" }));
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(Admin.updateSettings).toHaveBeenCalledTimes(1));
    expect((Admin.updateSettings as jest.Mock).mock.calls[0][0].body).toEqual({ [key]: "" });
  });
});
