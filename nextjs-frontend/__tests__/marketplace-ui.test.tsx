import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom";
import type { ReactNode } from "react";

import { Extensions } from "@/app/openapi-client";
import { MarketplacePage } from "@/components/extensions/marketplace-page";
import { TooltipProvider } from "@/components/ui/tooltip";

jest.mock("@/app/openapi-client", () => ({
  Extensions: {
    listMarketplaces: jest.fn(),
    suggestedMarketplaces: jest.fn(),
    addMarketplace: jest.fn(),
    refreshMarketplace: jest.fn(),
    deleteMarketplace: jest.fn(),
    listMarketplacePlugins: jest.fn(),
    previewMarketplacePlugin: jest.fn(),
    installMarketplacePlugin: jest.fn(),
    importLink: jest.fn(),
    importArchive: jest.fn(),
  },
}));
jest.mock("next-auth/react", () => ({ useSession: () => ({ data: { accessToken: "t" } }) }));
const toast = jest.fn();
jest.mock("@/components/ui/toast", () => ({ useToast: () => toast }));
let admin = true;
jest.mock("@/lib/hooks/session", () => ({ useArchive: () => ({ admin }) }));

const ok = (data: unknown) => Promise.resolve({ data, response: { ok: true, status: 200 } });
const m = (f: unknown) => f as jest.Mock;

function wrap(ui: ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <TooltipProvider>{ui}</TooltipProvider>
    </QueryClientProvider>,
  );
}

const market = {
  id: 1,
  name: "anthropic-agent-skills",
  title: "Anthropic skills",
  description: "Anthropic example skills",
  source: "https://github.com/anthropics/skills",
  plugins: 2,
};
const plugins = [
  { name: "document-skills", description: "Documents", installable: true, installed: null },
  { name: "from-npm", installable: false, why_not: "npm" },
];
const preview = {
  manifest: {
    name: "document_skills",
    title: "document-skills",
    description: "Documents",
    items: [{ kind: "skill", name: "pdf", description: "PDF files" }],
  },
  skipped: [{ kind: "hooks", name: "hooks", why: "they run commands" }],
  source: "https://github.com/anthropics/skills",
};

beforeEach(() => {
  jest.clearAllMocks();
  admin = true;
  m(Extensions.listMarketplaces).mockReturnValue(ok([market]));
  m(Extensions.suggestedMarketplaces).mockReturnValue(
    ok([
      {
        name: "claude-plugins-official",
        title: "Anthropic plugins",
        source: "https://github.com/anthropics/claude-plugins-official",
      },
    ]),
  );
  m(Extensions.listMarketplacePlugins).mockReturnValue(ok(plugins));
  m(Extensions.previewMarketplacePlugin).mockReturnValue(ok(preview));
  m(Extensions.installMarketplacePlugin).mockReturnValue(ok({ id: 9, version: 1, skipped: [] }));
  m(Extensions.addMarketplace).mockReturnValue(ok({ ok: true, id: 2 }));
  m(Extensions.importLink).mockReturnValue(ok(preview));
});

test("browses a marketplace, previews a plugin and installs it", async () => {
  wrap(<MarketplacePage />);
  expect(await screen.findByText("document-skills")).toBeInTheDocument();
  expect(screen.getByText("Not supported yet")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Install" }));
  expect(await screen.findByText("pdf")).toBeInTheDocument();
  expect(screen.getByText(/they run commands/)).toBeInTheDocument();
  expect(Extensions.previewMarketplacePlugin).toHaveBeenCalledWith(
    expect.objectContaining({ path: { mid: 1, name: "document-skills" } }),
  );
  const install = screen.getAllByRole("button", { name: "Install" }).at(-1)!;
  await waitFor(() => expect(install).toBeEnabled());
  fireEvent.click(install);
  await waitFor(() => expect(Extensions.installMarketplacePlugin).toHaveBeenCalled());
  await waitFor(() => expect(toast).toHaveBeenCalledWith(expect.objectContaining({ title: "Installed" })));
});

test("an admin adds a suggested marketplace; others don't see the form", async () => {
  const { unmount } = wrap(<MarketplacePage />);
  fireEvent.click(await screen.findByRole("button", { name: "Add Anthropic plugins" }));
  await waitFor(() =>
    expect(Extensions.addMarketplace).toHaveBeenCalledWith(
      expect.objectContaining({
        body: { source: "https://github.com/anthropics/claude-plugins-official", title: "Anthropic plugins" },
      }),
    ),
  );
  unmount();
  admin = false;
  wrap(<MarketplacePage />);
  expect(await screen.findByText("document-skills")).toBeInTheDocument();
  expect(screen.queryByLabelText("Add a marketplace")).not.toBeInTheDocument();
});

test("imports from a link after a preview", async () => {
  wrap(<MarketplacePage />);
  fireEvent.change(screen.getByLabelText("From a link"), { target: { value: "acme/tools" } });
  fireEvent.click(screen.getByRole("button", { name: "Preview" }));
  await waitFor(() =>
    expect(Extensions.importLink).toHaveBeenCalledWith(
      expect.objectContaining({ body: { url: "acme/tools", preview: true } }),
    ),
  );
  expect(await screen.findByText("pdf")).toBeInTheDocument();
});
