import { QueryClient, QueryObserver } from "@tanstack/react-query";

import { refetchFresh } from "@/components/chat/data";

jest.mock("@/lib/api/browser", () => ({ data: jest.fn(), useApiClient: jest.fn() }));

describe("a conversation read again after its answer is saved", () => {
  it("shows the answer even when the conversation's first read left before it was saved", async () => {
    // A new conversation: the page opens it (its first read) while the answer is still streaming; the server reads it
    // before the answer is saved, and that reply arrives only after the stream has ended.
    const saved: string[] = ["Who said what about Raspberry Pi?"];
    const late: Array<() => void> = [];
    const reads: number[] = [];
    const queryFn = () => {
      const now = [...saved]; // what the server has when the read reaches it
      reads.push(now.length);
      return reads.length === 1
        ? new Promise<string[]>((resolve) => late.push(() => resolve(now)))
        : Promise.resolve(now);
    };
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const observer = new QueryObserver(qc, { queryKey: ["chat", 2], queryFn, staleTime: 10_000 });
    const unsubscribe = observer.subscribe(() => undefined);
    expect(reads).toEqual([1]); // the first read is on its way

    saved.push("This is the survey's stand-in model answering."); // the stream ends with `done`: the answer is saved
    const refreshed = refetchFresh(qc, ["chat", 2]);
    late.forEach((reply) => reply()); // the first read's reply, from before the save, comes back
    await refreshed;

    expect(reads).toEqual([1, 2]); // read again after the save, not only the read already on its way
    expect(observer.getCurrentResult().data).toEqual([
      "Who said what about Raspberry Pi?",
      "This is the survey's stand-in model answering.",
    ]);
    unsubscribe();
    qc.clear();
  });

  it("reads a conversation that is already showing again", async () => {
    let answer = "first";
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const observer = new QueryObserver(qc, { queryKey: ["chat", 3], queryFn: async () => answer, staleTime: 10_000 });
    const unsubscribe = observer.subscribe(() => undefined);
    await qc.getQueryCache().find({ queryKey: ["chat", 3] })?.promise;
    answer = "second";
    await refetchFresh(qc, ["chat", 3]);
    expect(observer.getCurrentResult().data).toBe("second");
    unsubscribe();
    qc.clear();
  });
});
