import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom";
import type { ReactNode } from "react";

import { Pipelines, Sources } from "@/app/openapi-client";
import { FolderBrowser } from "@/components/sources/folder-browser";
import { SourcesPage } from "@/components/sources/sources-page";
import { TooltipProvider } from "@/components/ui/tooltip";

jest.mock("@/app/openapi-client", () => ({
  Sources: {
    listBackends: jest.fn(),
    listSources: jest.fn(),
    listWatches: jest.fn(),
    createSource: jest.fn(),
    browseSource: jest.fn(),
    previewWatch: jest.fn(),
  },
  Pipelines: { listPipelines: jest.fn() },
}));
jest.mock("next-auth/react", () => ({ useSession: () => ({ data: { accessToken: "t" } }) }));
const replace = jest.fn();
let search = "";
jest.mock("next/navigation", () => ({
  useRouter: () => ({ push: jest.fn(), replace }),
  usePathname: () => "/sources",
  useSearchParams: () => new URLSearchParams(search),
}));
jest.mock("@/lib/hooks/session", () => ({
  useArchive: () => ({
    admin: true,
    can: () => true,
    namespace: "home",
    namespaces: [{ id: 3, name: "home" }],
  }),
}));
jest.mock("@/components/ui/toast", () => ({ useToast: () => jest.fn() }));

const ok = (data: unknown) => Promise.resolve({ data, response: { ok: true, status: 200 } });
const m = (f: unknown) => f as jest.Mock;
const LOCAL = { id: 5, name: "Folder on this machine", type: "local", params: {}, health: { ok: true } };

function wrap(ui: ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <TooltipProvider>{ui}</TooltipProvider>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  search = "";
  replace.mockReset();
  m(Sources.listBackends).mockReturnValue(ok({ local: { label: "Folder on this machine", fields: {}, secrets: [] } }));
  m(Sources.listWatches).mockReturnValue(ok([]));
  m(Sources.browseSource).mockReturnValue(ok([{ path: "/audio", rel: "/audio", name: "/audio", dir: true }]));
  m(Sources.previewWatch).mockReturnValue(ok({ files: 0, audio: 0, transcripts: 0, documents: 0, images: 0 }));
  m(Sources.createSource).mockReset();
  m(Pipelines.listPipelines).mockReturnValue(ok({ pipelines: [] }));
});

test("an allowed folder is watched from the list, without opening it first", async () => {
  const onWatch = jest.fn();
  wrap(<FolderBrowser open source={LOCAL as never} onOpenChange={() => undefined} onWatch={onWatch} canWatch />);
  fireEvent.click(await screen.findByRole("button", { name: "Watch /audio" }));
  expect(onWatch).toHaveBeenCalledWith("/audio");
  // opening it still works, to watch a folder inside
  expect(screen.getByRole("button", { name: "/audio" })).toBeInTheDocument();
});

test("Watch a local folder opens this machine's allowed folders on its existing connection", async () => {
  search = "watch=local";
  m(Sources.listSources).mockReturnValue(ok([LOCAL]));
  wrap(<SourcesPage />);
  expect(await screen.findByRole("dialog", { name: "Browse Folder on this machine" })).toBeInTheDocument();
  expect(replace).toHaveBeenCalledWith("/sources");
  expect(Sources.createSource).not.toHaveBeenCalled();
  fireEvent.click(await screen.findByRole("button", { name: "Watch /audio" }));
  expect(await screen.findByRole("dialog", { name: "Watch Folder on this machine:/audio" })).toBeInTheDocument();
});

test("Watch a local folder makes this machine's connection when there's none, with nothing to fill in", async () => {
  search = "watch=local";
  m(Sources.listSources)
    .mockReturnValueOnce(ok([]))
    .mockReturnValue(ok([LOCAL]));
  m(Sources.createSource).mockReturnValue(ok({ id: 5, health: { ok: true } }));
  wrap(<SourcesPage />);
  expect(await screen.findByRole("dialog", { name: "Browse Folder on this machine" })).toBeInTheDocument();
  await waitFor(() => expect(Sources.createSource).toHaveBeenCalledTimes(1));
  expect(m(Sources.createSource).mock.calls[0][0].body).toEqual({
    type: "local",
    name: "Folder on this machine",
    params: {},
    secrets: {},
  });
});
