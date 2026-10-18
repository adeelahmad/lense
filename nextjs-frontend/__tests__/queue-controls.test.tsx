import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom";
import type { ReactNode } from "react";

import { Jobs } from "@/app/openapi-client";
import { QueueControls } from "@/components/activity/queue-controls";
import { ToastProvider } from "@/components/ui/toast";
import { TooltipProvider } from "@/components/ui/tooltip";

jest.mock("@/app/openapi-client", () => ({
  Jobs: { cancelJobs: jest.fn(), retryJobs: jest.fn(), moveJob: jest.fn() },
}));
jest.mock("next-auth/react", () => ({ useSession: () => ({ data: { accessToken: "t" } }) }));

const ok = (data: unknown) => Promise.resolve({ data, response: { ok: true, status: 200 } });
const m = (f: unknown) => f as jest.Mock;

function wrap(ui: ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <TooltipProvider>
        <ToastProvider>{ui}</ToastProvider>
      </TooltipProvider>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  m(Jobs.cancelJobs)
    .mockReset()
    .mockReturnValue(ok({ ok: true, jobs: [4, 5], stopping: [], skipped: 0 }));
  m(Jobs.retryJobs)
    .mockReset()
    .mockReturnValue(ok({ ok: true, jobs: [7], stopping: [], skipped: 1 }));
});

test("clearing the queue asks first, cancels the namespace's waiting runs, and undoes", async () => {
  wrap(<QueueControls namespace="family" queued={2} failed={0} allowed />);
  expect(screen.queryByRole("button", { name: "Retry failed" })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Clear queue" }));
  expect(await screen.findByText("Cancels the 2 runs waiting in family. Runs already running carry on.")).toBeVisible();
  expect(Jobs.cancelJobs).not.toHaveBeenCalled();
  fireEvent.click(screen.getAllByRole("button", { name: "Clear queue" }).at(-1)!);
  await screen.findByText("Cleared 2 runs from the queue");
  expect(m(Jobs.cancelJobs).mock.calls[0][0].body).toEqual({ namespace: "family" });
  fireEvent.click(screen.getByRole("button", { name: "Undo" }));
  await waitFor(() => expect(m(Jobs.retryJobs).mock.calls[0][0].body).toEqual({ jobs: [4, 5], status: ["cancelled"] }));
});

test("retry failed retries every failed run at once, says what it left alone, and undoes", async () => {
  wrap(<QueueControls queued={0} failed={3} allowed />);
  expect(screen.queryByRole("button", { name: "Clear queue" })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Retry failed" }));
  await screen.findByText("Retrying 1 run");
  expect(screen.getByText("1 run left alone: the asset was deleted or already has a run going.")).toBeVisible();
  expect(m(Jobs.retryJobs).mock.calls[0][0].body).toEqual({ namespace: undefined });
  fireEvent.click(screen.getByRole("button", { name: "Undo" }));
  await waitFor(() => expect(m(Jobs.cancelJobs).mock.calls[0][0].body).toEqual({ jobs: [7] }));
});

test("nothing shows for someone who can't process, or with nothing to clear or retry", () => {
  const first = wrap(<QueueControls queued={2} failed={2} allowed={false} />);
  expect(screen.queryByRole("button")).not.toBeInTheDocument();
  first.unmount();
  wrap(<QueueControls queued={0} failed={0} allowed />);
  expect(screen.queryByRole("button")).not.toBeInTheDocument();
});
