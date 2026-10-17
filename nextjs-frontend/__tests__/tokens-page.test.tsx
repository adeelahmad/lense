import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom";

import { Namespaces, Tokens } from "@/app/openapi-client";
import { TokensPage } from "@/components/account/tokens";
import { TooltipProvider } from "@/components/ui/tooltip";

jest.mock("@/app/openapi-client", () => ({
  Tokens: { listTokens: jest.fn(), tokenLimits: jest.fn(), createToken: jest.fn(), renameToken: jest.fn() },
  Namespaces: { listNamespaces: jest.fn() },
}));
jest.mock("next-auth/react", () => ({ useSession: () => ({ data: { accessToken: "t" } }) }));
let params = new URLSearchParams();
jest.mock("next/navigation", () => ({ useSearchParams: () => params }));
jest.mock("@/components/ui/toast", () => ({ useToast: () => jest.fn() }));

const ok = (data: unknown) => Promise.resolve({ data, response: { ok: true, status: 200 } });
const TOKEN = {
  id: 3,
  name: "Read only token",
  scope: "read",
  prefix: "la_abcdef",
  created_at: "2030-10-01T03:00:00Z",
  expires_at: null,
  last_used_at: null,
};

const show = () =>
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <TooltipProvider>
        <TokensPage />
      </TooltipProvider>
    </QueryClientProvider>,
  );

beforeEach(() => {
  jest.clearAllMocks();
  params = new URLSearchParams();
  (Namespaces.listNamespaces as jest.Mock).mockImplementation(() => ok([{ name: "pods" }, { name: "calls" }]));
  (Tokens.tokenLimits as jest.Mock).mockImplementation(() =>
    ok({ default_days: 90, max_days: 365, never_expire: false }),
  );
  (Tokens.createToken as jest.Mock).mockImplementation(() => ok({ id: 4, token: "la_secret" }));
  (Tokens.renameToken as jest.Mock).mockImplementation(() => ok({ ok: true }));
});

describe("API tokens", () => {
  it("opens the form when there are none yet, and Create uses the guessed name", async () => {
    (Tokens.listTokens as jest.Mock).mockImplementation(() => ok([]));
    show();
    const name = await screen.findByRole("textbox", { name: "Name" });
    expect(name).toHaveAttribute("placeholder", "Read only token");
    const create = screen.getByRole("button", { name: "Create token" });
    await waitFor(() => expect(create).toBeEnabled());
    fireEvent.click(create);
    await waitFor(() => expect(Tokens.createToken).toHaveBeenCalled());
    expect((Tokens.createToken as jest.Mock).mock.calls[0][0].body).toMatchObject({
      name: "Read only token",
      days: 90,
    });
  });

  it("guesses a name no token has, and renames one in the list", async () => {
    (Tokens.listTokens as jest.Mock).mockImplementation(() => ok([TOKEN]));
    show();
    fireEvent.click(await screen.findByRole("button", { name: "Rename token" }));
    const field = screen.getByRole("textbox", { name: "Token name" });
    fireEvent.change(field, { target: { value: "CI runner" } });
    fireEvent.submit(field);
    await waitFor(() =>
      expect((Tokens.renameToken as jest.Mock).mock.calls[0][0]).toMatchObject({
        path: { token_id: 3 },
        body: { name: "CI runner" },
      }),
    );
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "New token" }));
    expect(await screen.findByRole("textbox", { name: "Name" })).toHaveAttribute("placeholder", "Read only token 2");
  });

  it("makes a key that opens one namespace alone, from Connect apps' link", async () => {
    params = new URLSearchParams("new=1&namespace=pods");
    (Tokens.listTokens as jest.Mock).mockImplementation(() =>
      ok([TOKEN, { ...TOKEN, id: 5, name: "Old", namespace: "calls" }]),
    );
    show();
    expect(await screen.findByText("calls alone")).toBeInTheDocument(); // the list says what each key opens
    const name = await screen.findByRole("textbox", { name: "Name" });
    expect(name).toHaveAttribute("placeholder", "pods app key");
    expect(await screen.findByRole("combobox", { name: "Opens" })).toHaveValue("pods");
    const create = screen.getByRole("button", { name: "Create token" });
    await waitFor(() => expect(create).toBeEnabled());
    fireEvent.click(create);
    await waitFor(() => expect(Tokens.createToken).toHaveBeenCalled());
    expect((Tokens.createToken as jest.Mock).mock.calls[0][0].body).toMatchObject({
      name: "pods app key",
      namespace: "pods",
    });
    expect(await screen.findByText(/pods alone ·/)).toBeInTheDocument();
  });
});
