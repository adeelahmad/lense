import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom";

import { Routines } from "@/app/openapi-client";
import type { GraphChange } from "@/app/openapi-client/types.gen";
import { ChangeCard, typeName } from "@/components/routines/graph-changes";
import { ToastProvider } from "@/components/ui/toast";
import { TooltipProvider } from "@/components/ui/tooltip";

jest.mock("@/app/openapi-client", () => ({
  Routines: { acceptGraphChange: jest.fn(), dismissGraphChange: jest.fn(), undoGraphChange: jest.fn() },
}));
jest.mock("next-auth/react", () => ({ useSession: () => ({ data: { accessToken: "t" } }) }));
jest.mock("@/lib/hooks/session", () => ({ useArchive: () => ({ can: () => true }), needRole: () => "" }));

const ok = (data: unknown) => Promise.resolve({ data, response: { ok: true, status: 200 } });
const side = {
  id: 5,
  name: "Northwind Labs",
  type: "TERM",
  namespace: "pods",
  mentions: 3,
  samples: ["Northwind Labs again."],
};

function card(ch: Partial<GraphChange>) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const full = { id: 9, spaces: [1], status: "proposed", a: side, ...ch } as GraphChange;
  return render(
    <QueryClientProvider client={qc}>
      <TooltipProvider>
        <ToastProvider>
          <ChangeCard ch={full} />
        </ToastProvider>
      </TooltipProvider>
    </QueryClientProvider>,
  );
}

describe("graph changes about one entity", () => {
  beforeEach(() => jest.mocked(Routines.acceptGraphChange).mockImplementation(() => ok({ ok: true }) as never));

  it("say a type change in words and make it", async () => {
    card({ kind: "retype", to: "ORG", reason: "Organisation: its name says so", confidence: 0.9 });
    expect(screen.getByText("Type: Term → Organisation?")).toBeInTheDocument();
    expect(screen.getByText("Leave it")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Make it Organisation" }));
    await waitFor(() => expect(Routines.acceptGraphChange).toHaveBeenCalledWith(expect.objectContaining({ body: {} })));
    expect(await screen.findByText("Northwind Labs: Organisation now")).toBeInTheDocument();
  });

  it("offer to hide noise, and undo it once made", () => {
    card({
      kind: "hide",
      status: "applied",
      reason: "filler words, not a name",
      verdict: { agree: true, confidence: 0.96 },
    });
    expect(screen.getByText("Hide as noise?")).toBeInTheDocument();
    expect(screen.getByText("Noise")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Undo/ })).toBeInTheDocument();
  });

  it("name a namespace's own types", () => {
    expect(typeName("CLIENT_TEAM")).toBe("Client team");
    expect(typeName("PERSON")).toBe("Person");
  });
});
