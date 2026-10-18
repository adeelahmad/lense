import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom";

import { Admin } from "@/app/openapi-client";
import { SettingsSection } from "@/components/settings/section";
import type { SectionId, SettingsView } from "@/components/settings/model";
import { TooltipProvider } from "@/components/ui/tooltip";

jest.mock("@/app/openapi-client", () => ({
  Admin: {
    updateSettings: jest.fn(),
    getSecrets: jest.fn(),
    listVaultItems: jest.fn(),
    testSecrets: jest.fn(),
    testMail: jest.fn(),
  },
  Metadata: {},
}));
jest.mock("next-auth/react", () => ({ useSession: () => ({ data: { accessToken: "t" } }) }));
jest.mock("next/navigation", () => ({ useRouter: () => ({ push: jest.fn() }) }));
jest.mock("@/lib/hooks/session", () => ({ useArchive: () => ({ namespaces: [] }) }));
jest.mock("@/components/ui/toast", () => ({ useToast: () => jest.fn() }));

const ok = (data: unknown) => Promise.resolve({ data, response: { ok: true, status: 200 } });
const REF = "vaultwarden://Mail/password";
const VAULT = {
  id: "vaultwarden",
  label: "Vaultwarden",
  scheme: "vaultwarden",
  configured: true,
  url: "https://vault.example.com",
  folder: "Lens",
};
const SECRETS = {
  providers: [{ id: "builtin", label: "Saved in Lens", scheme: null, configured: true }, VAULT],
  references: [
    {
      section: "mail",
      key: "password",
      ref: REF,
      provider: "vaultwarden",
      read: true,
      error: null,
      read_at: new Date().toISOString(),
      missing: false,
    },
  ],
  fields: [],
};

const mail = (password: unknown) =>
  ({
    mail: {
      values: {
        server: "smtp.example.com",
        port: 587,
        security: "starttls",
        username: "lens",
        password,
        from_address: "lens@example.com",
        from_name: "Lens",
      },
    },
  }) as unknown as SettingsView;

const secrets = (values: Record<string, unknown>) =>
  ({
    secrets: {
      values: {
        cache_minutes: 10,
        vaultwarden_url: null,
        vaultwarden_email: null,
        vaultwarden_client_id: null,
        vaultwarden_client_secret: null,
        vaultwarden_password: null,
        vaultwarden_folder: "Lens",
        ...values,
      },
    },
  }) as unknown as SettingsView;

function show(id: SectionId, view: SettingsView) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <TooltipProvider>
        <SettingsSection id={id} view={view} onErrors={() => undefined} />
      </TooltipProvider>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  (Admin.updateSettings as jest.Mock).mockReset().mockImplementation(() => ok({ ok: true }));
  (Admin.getSecrets as jest.Mock).mockReset().mockImplementation(() => ok(SECRETS));
  (Admin.testSecrets as jest.Mock).mockReset();
  (Admin.listVaultItems as jest.Mock).mockReset().mockImplementation(() =>
    ok({
      provider: "vaultwarden",
      items: [
        {
          name: "Mail",
          type: "login",
          fields: ["username", "password"],
          ref: REF,
          refs: { username: "vaultwarden://Mail/username", password: REF },
        },
      ],
    }),
  );
});

describe("Settings › Secrets", () => {
  it("sets up Vaultwarden, keeping the key and password write-only", async () => {
    show("secrets", secrets({}));
    fireEvent.change(screen.getByLabelText("Vaultwarden address"), { target: { value: "https://vault.example.com" } });
    fireEvent.change(screen.getByLabelText("Lens’s account"), { target: { value: "lens@example.com" } });
    fireEvent.change(screen.getByLabelText("API key client_id"), { target: { value: "user.1234" } });
    fireEvent.change(screen.getByLabelText("API key client_secret"), { target: { value: "s3cret" } });
    fireEvent.change(screen.getByLabelText("Master password"), { target: { value: "correct horse" } });
    // the vault's own sign-in can't be a reference, so there's no picker here
    expect(screen.queryByRole("button", { name: /From Vaultwarden/ })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(Admin.updateSettings).toHaveBeenCalledTimes(1));
    expect((Admin.updateSettings as jest.Mock).mock.calls[0][0]).toMatchObject({
      path: { section: "secrets" },
      body: {
        vaultwarden_url: "https://vault.example.com",
        vaultwarden_email: "lens@example.com",
        vaultwarden_client_id: "user.1234",
        vaultwarden_client_secret: "s3cret",
        vaultwarden_password: "correct horse",
      },
    });
  });

  it("refuses an address with a password in it before saving", () => {
    show("secrets", secrets({}));
    fireEvent.change(screen.getByLabelText("Vaultwarden address"), {
      target: { value: "https://me:pw@vault.example.com" },
    });
    expect(screen.getByText(/no user name, password or query/)).toBeInTheDocument();
  });

  it("tests the saved sign-in and lists the fields that refer to the vault", async () => {
    (Admin.testSecrets as jest.Mock).mockImplementation(() =>
      ok({ ok: true, items: 3, folder: "Lens", account: "lens@example.com" }),
    );
    show(
      "secrets",
      secrets({
        vaultwarden_url: "https://vault.example.com",
        vaultwarden_email: "lens@example.com",
        vaultwarden_client_id: "user.1234",
        vaultwarden_client_secret: { secret: true, set: true },
        vaultwarden_password: { secret: true, set: true },
      }),
    );
    expect(await screen.findByText(REF)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Email › Password" })).toHaveAttribute(
      "href",
      "/settings/mail?field=mail.password",
    );
    expect(screen.getByText(/Read from Vaultwarden/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Test" }));
    expect(await screen.findByText(/3 item\(s\) in the folder Lens of lens@example.com/)).toBeInTheDocument();
    expect(Admin.testSecrets).toHaveBeenCalledWith(expect.objectContaining({ body: { provider: "vaultwarden" } }));
  });
});

describe("a secret field that refers to the vault", () => {
  it("shows its reference and whether it was read, never a value", async () => {
    show(
      "mail",
      mail({
        secret: true,
        set: true,
        ref: REF,
        provider: "vaultwarden",
        read: false,
        error: "there's no item called Mail",
      }),
    );
    expect(screen.getByText(REF)).toBeInTheDocument();
    expect(screen.getByText(/From Vaultwarden/)).toBeInTheDocument();
    expect(screen.getByText(/Not read: there's no item called Mail/)).toBeInTheDocument();
  });

  it("is picked from the vault's items, and only the reference is saved", async () => {
    show("mail", mail({ secret: true, set: true }));
    fireEvent.click(screen.getByRole("button", { name: "Replace" }));
    fireEvent.click(await screen.findByRole("button", { name: /From Vaultwarden/ }));
    fireEvent.click(await screen.findByRole("button", { name: "password" }));
    const input = screen.getByLabelText("Password");
    expect(input).toHaveValue(REF);
    expect(input).toHaveAttribute("type", "text"); // a reference isn't a secret to hide
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(Admin.updateSettings).toHaveBeenCalledTimes(1));
    expect((Admin.updateSettings as jest.Mock).mock.calls[0][0]).toMatchObject({
      path: { section: "mail" },
      body: { password: REF },
    });
    expect(Admin.listVaultItems).toHaveBeenCalledWith(
      expect.objectContaining({ query: expect.objectContaining({ provider: "vaultwarden" }) }),
    );
  });

  it("Enter in the vault's search picks the first item", async () => {
    show("mail", mail(null));
    fireEvent.click(await screen.findByRole("button", { name: /From Vaultwarden/ }));
    const search = screen.getByRole("searchbox", { name: "Find an item in Vaultwarden" });
    await screen.findByText("Mail");
    fireEvent.keyDown(search, { key: "Enter" });
    expect(screen.getByLabelText("Password")).toHaveValue(REF);
  });

  it("has no picker when no vault is set up", async () => {
    (Admin.getSecrets as jest.Mock).mockImplementation(() =>
      ok({ ...SECRETS, providers: [{ ...VAULT, configured: false }], references: [] }),
    );
    show("mail", mail(null));
    await waitFor(() => expect(Admin.getSecrets).toHaveBeenCalled());
    expect(screen.queryByRole("button", { name: /From Vaultwarden/ })).not.toBeInTheDocument();
  });
});
