import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom";

import { Sources } from "@/app/openapi-client";
import { ConnectionDialog } from "@/components/sources/connection-dialog";
import { TooltipProvider } from "@/components/ui/tooltip";

jest.mock("@/app/openapi-client", () => ({
  Sources: { createSource: jest.fn(), updateSource: jest.fn(), testSource: jest.fn(), deleteSource: jest.fn() },
}));
jest.mock("next-auth/react", () => ({ useSession: () => ({ data: { accessToken: "t" } }) }));
jest.mock("@/components/ui/toast", () => ({ useToast: () => jest.fn() }));

const ok = (data: unknown) => Promise.resolve({ data, response: { ok: true, status: 200 } });
const BACKENDS = { local: { label: "This machine", fields: {}, secrets: [] } };

function show(onSaved: (id: number) => void) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <TooltipProvider>
        <ConnectionDialog open onOpenChange={() => undefined} backends={BACKENDS} onSaved={onSaved} />
      </TooltipProvider>
    </QueryClientProvider>,
  );
}

describe("adding a connection", () => {
  beforeEach(() => {
    (Sources.createSource as jest.Mock).mockReset().mockImplementation(() => ok({ id: 7, health: { ok: true } }));
    (Sources.updateSource as jest.Mock).mockReset().mockImplementation(() => ok({ id: 7, health: { ok: true } }));
  });

  it("goes type → details → tested and named, with no extra steps", async () => {
    const saved = jest.fn();
    show(saved);
    fireEvent.click(screen.getByRole("radio", { name: /This machine/ })); // one click on the type moves on
    fireEvent.click(screen.getByRole("button", { name: "Test connection" }));
    const name = await screen.findByLabelText("Name");
    expect(name).toHaveValue("Folder on this machine");
    expect((Sources.createSource as jest.Mock).mock.calls[0][0].body).toMatchObject({
      type: "local",
      name: "Folder on this machine",
    });
    fireEvent.click(screen.getByRole("button", { name: "Done" }));
    await waitFor(() => expect(saved).toHaveBeenCalledWith(7));
    expect(Sources.updateSource).not.toHaveBeenCalled(); // the suggested name was kept: nothing to save again
  });

  it("saves a name changed on the test screen", async () => {
    const saved = jest.fn();
    show(saved);
    fireEvent.click(screen.getByRole("radio", { name: /This machine/ }));
    fireEvent.click(screen.getByRole("button", { name: "Test connection" }));
    fireEvent.change(await screen.findByLabelText("Name"), { target: { value: "Scans" } });
    fireEvent.click(screen.getByRole("button", { name: "Done" }));
    await waitFor(() => expect(saved).toHaveBeenCalledWith(7));
    expect((Sources.updateSource as jest.Mock).mock.calls[0][0]).toMatchObject({
      path: { sid: 7 },
      body: { name: "Scans" },
    });
  });
});
