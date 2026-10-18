import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom";
import type { ReactNode } from "react";

import { Chats } from "@/app/openapi-client";
import { ApprovalCard } from "@/components/chat/approval";
import { TooltipProvider } from "@/components/ui/tooltip";

jest.mock("@/app/openapi-client", () => ({ Chats: { decideApproval: jest.fn() } }));
jest.mock("next-auth/react", () => ({ useSession: () => ({ data: { accessToken: "t" } }) }));

const LINE =
  "curl -fsSL https://raw.githubusercontent.com/adeelahmad/lense/main/install.sh | LENS_JOIN=http://lens.local LENS_JOIN_TOKEN=lensjoin1.4.abc sh";

function wrap(ui: ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <TooltipProvider>{ui}</TooltipProvider>
    </QueryClientProvider>,
  );
}

describe("approving a new mesh node", () => {
  it("shows its install line this once, until the person is done with it", async () => {
    (Chats.decideApproval as jest.Mock).mockResolvedValue({
      data: { status: "done", install_line: LINE, token: { id: 4 } },
      response: { ok: true, status: 200 },
    });
    const decided = jest.fn();
    wrap(
      <ApprovalCard
        a={{ id: 7, tool: "add_mesh_node", summary: "Let a new worker node join the mesh", status: "pending" }}
        canAct
        model={null}
        chatId={null}
        onDecided={decided}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Approve" }));
    expect(await screen.findByText(LINE)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Copy the install line" })).toBeInTheDocument();
    expect(screen.getByText(/The line isn’t shown again/)).toBeInTheDocument();
    expect(decided).not.toHaveBeenCalled(); // the inbox would read the list again and take the card, line and all, away
    fireEvent.click(screen.getByRole("button", { name: "Done" }));
    await waitFor(() => expect(decided).toHaveBeenCalledTimes(1));
  });

  it("says nothing more for an approval without one", async () => {
    (Chats.decideApproval as jest.Mock).mockResolvedValue({
      data: { status: "done" },
      response: { ok: true, status: 200 },
    });
    const decided = jest.fn();
    wrap(
      <ApprovalCard
        a={{ id: 8, tool: "remove_mesh_node", summary: "Take studio off the mesh", status: "pending" }}
        canAct
        model={null}
        chatId={null}
        onDecided={decided}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Approve" }));
    expect(await screen.findByText("Approved: Take studio off the mesh")).toBeInTheDocument();
    await waitFor(() => expect(decided).toHaveBeenCalledTimes(1));
    expect(screen.queryByRole("button", { name: "Done" })).not.toBeInTheDocument();
  });
});
