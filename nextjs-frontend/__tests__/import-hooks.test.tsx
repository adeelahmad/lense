import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import "@testing-library/jest-dom";

import { ImportHooks } from "@/app/openapi-client";
import { hookExamples, ImportHooksSection, lastUsed } from "@/components/imports/import-hooks";
import { TooltipProvider } from "@/components/ui/tooltip";

jest.mock("@/app/openapi-client", () => ({
  ImportHooks: {
    listImportHooks: jest.fn(),
    createImportHook: jest.fn(),
    updateImportHook: jest.fn(),
    newImportHookToken: jest.fn(),
    deleteImportHook: jest.fn(),
  },
}));
jest.mock("next-auth/react", () => ({ useSession: () => ({ data: { accessToken: "t" } }) }));
jest.mock("@/components/ui/toast", () => ({ useToast: () => jest.fn() }));

const ok = (data: unknown) => Promise.resolve({ data, response: { ok: true, status: 200 } });
const HOOK = {
  id: 3,
  name: "Office scanner",
  namespace: "pods",
  enabled: true,
  token_tail: "Wx9z",
  used: 0,
  created_at: "2026-10-05T03:00:00Z",
};

function show(isOwner = true) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <TooltipProvider>
        <ImportHooksSection ns="pods" isOwner={isOwner} />
      </TooltipProvider>
    </QueryClientProvider>,
  );
}

describe("import webhooks", () => {
  beforeEach(() => {
    (ImportHooks.listImportHooks as jest.Mock).mockReset().mockImplementation(() => ok([HOOK]));
    (ImportHooks.createImportHook as jest.Mock)
      .mockReset()
      .mockImplementation(() => ok({ hook: { ...HOOK, id: 4, name: "Zapier" }, token: "lih_secret", path: "/x" }));
    (ImportHooks.updateImportHook as jest.Mock).mockReset().mockImplementation(() => ok({ ...HOOK, enabled: false }));
  });

  it("lists hooks with their token's tail and what they did", async () => {
    show();
    const list = await screen.findByRole("list", { name: "Import webhooks" });
    expect(within(list).getByText("Office scanner")).toBeInTheDocument();
    expect(within(list).getByText(/…Wx9z/)).toBeInTheDocument();
    expect(within(list).getByText("Nothing pushed yet")).toBeInTheDocument();
  });

  it("makes a hook and shows its token once", async () => {
    show();
    fireEvent.click(await screen.findByRole("button", { name: "New webhook" }));
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: " Zapier " } });
    fireEvent.click(screen.getByRole("button", { name: "Make webhook" }));
    await screen.findByText("Zapier is ready");
    expect((ImportHooks.createImportHook as jest.Mock).mock.calls[0][0]).toMatchObject({
      path: { name: "pods" },
      body: { name: "Zapier" },
    });
    expect(screen.getAllByText(/lih_secret/).length).toBeGreaterThan(0);
  });

  it("pauses a hook", async () => {
    show();
    fireEvent.click(await screen.findByRole("button", { name: "Pause Office scanner" }));
    await waitFor(() =>
      expect((ImportHooks.updateImportHook as jest.Mock).mock.calls[0][0]).toMatchObject({
        path: { name: "pods", hid: 3 },
        body: { enabled: false },
      }),
    );
  });

  it("shows nothing to someone who isn't an owner", () => {
    show(false);
    expect(ImportHooks.listImportHooks).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "New webhook" })).toHaveAttribute("aria-disabled", "true");
  });

  it("words examples and use", () => {
    const ex = hookExamples("https://lens.example", "lih_t");
    expect(ex.file).toContain("Authorization: Bearer lih_t");
    expect(ex.file).toContain("https://lens.example/api/v1/hooks/import?filename=notes.pdf");
    expect(ex.link).toContain('{"url": "https://example.org/report.pdf"}');
    expect(lastUsed({ used: 0, last_used_at: null })).toBe("Nothing pushed yet");
    expect(lastUsed({ used: 4, last_used_at: "2026-10-05T03:00:00Z" })).toMatch(/^4 imported, last /);
  });
});
