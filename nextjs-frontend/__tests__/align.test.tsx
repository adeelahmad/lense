import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom";

import { Resources } from "@/app/openapi-client";
import { AlignBanner } from "@/components/recording/align";
import { offerAlign, timesLabel } from "@/components/recording/align-model";
import type { JobInfo } from "@/components/recording/jobs";
import { TooltipProvider } from "@/components/ui/tooltip";

jest.mock("@/app/openapi-client", () => ({ Resources: { alignTranscript: jest.fn() } }));
jest.mock("next-auth/react", () => ({ useSession: () => ({ data: { accessToken: "t" } }) }));
const toast = jest.fn();
jest.mock("@/components/ui/toast", () => ({ useToast: () => toast }));

const m = (f: unknown) => f as jest.Mock;
const job = (status: string, type = "align") => ({ id: 3, status, steps: [{ type }] }) as unknown as JobInfo;
const estimated = { times: "estimated" as const };

function show(props: { editable?: boolean; jobs?: JobInfo[] } = {}) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <TooltipProvider>
        <AlignBanner rid={7} rec={estimated} hasAudio jobs={props.jobs ?? []} editable={props.editable ?? true} />
      </TooltipProvider>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  jest.clearAllMocks();
  m(Resources.alignTranscript).mockResolvedValue({ data: { ok: true, job: 12 }, response: { ok: true, status: 202 } });
});

describe("lining a transcript up with its audio", () => {
  it("is offered only for estimated times with audio, and not while a job is at it", () => {
    expect(offerAlign(estimated, true, [])).toBe(true);
    expect(offerAlign(estimated, false, [])).toBe(false); // attach the audio first
    expect(offerAlign({ times: "aligned" }, true, [])).toBe(false);
    expect(offerAlign({ times: null }, true, [])).toBe(false); // they came with it
    expect(offerAlign(estimated, true, [job("running")])).toBe(false);
    expect(offerAlign(estimated, true, [job("held")])).toBe(false);
    expect(offerAlign(estimated, true, [job("failed"), job("running", "analyze")])).toBe(true);
  });

  it("says whether the times are estimated or from the audio", () => {
    expect(timesLabel(estimated)).toBe("Times estimated");
    expect(timesLabel({ times: "aligned", aligned: { matched: 0.754 } })).toBe(
      "Times from the audio · 75% of words heard",
    );
    expect(timesLabel({ times: "aligned" })).toBe("Times from the audio");
    expect(timesLabel({ times: null })).toBeNull();
  });

  it("lines it up in one click, then gets out of the way", async () => {
    show();
    expect(screen.getByText("These times are estimated from the text.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Line up with the audio" }));
    await waitFor(() =>
      expect(Resources.alignTranscript).toHaveBeenCalledWith(expect.objectContaining({ path: { rid: 7 } })),
    );
    await waitFor(() =>
      expect(screen.queryByRole("button", { name: "Line up with the audio" })).not.toBeInTheDocument(),
    );
    expect(toast).toHaveBeenCalledWith(expect.objectContaining({ title: "Lining up with the audio" }));
  });

  it("is for editors only", () => {
    show({ editable: false });
    expect(screen.getByRole("button", { name: "Line up with the audio" })).toBeDisabled();
  });
});
