import { QueryClient, QueryObserver } from "@tanstack/react-query";

import { refreshAnswered } from "@/components/chat/data";

jest.mock("next-auth/react", () => ({ useSession: () => ({ data: { accessToken: "t" } }) }));

/** A conversation as GET /chats/{id} gives it: the question, then the question with its answer. */
const asked = { id: 2, messages: [{ id: 10, role: "user", content: "Who said what?" }] };
const answered = { id: 2, messages: [...asked.messages, { id: 11, role: "assistant", content: "They did." }] };

function deferred<T>() {
  let resolve!: (v: T) => void;
  const promise = new Promise<T>((r) => (resolve = r));
  return { promise, resolve };
}

test("a conversation first loaded while its answer streamed is read again once the load lacks the answer", async () => {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: 0 } } });
  const first = deferred<typeof asked>();
  const queryFn = jest
    .fn()
    .mockImplementationOnce(() => first.promise)
    .mockResolvedValue(answered);
  // the page moved to /chat/2 and is loading the conversation (the question only, so far) as the answer streams
  const observer = new QueryObserver(qc, { queryKey: ["chat", 2], queryFn });
  const unsubscribe = observer.subscribe(() => {});
  await Promise.resolve();
  expect(queryFn).toHaveBeenCalledTimes(1);
  // the answer ends: the refetch asked for now is folded into the load still in flight
  const refreshed = refreshAnswered(qc, 2, 11);
  first.resolve(asked);
  await refreshed;
  expect(queryFn).toHaveBeenCalledTimes(2);
  expect(qc.getQueryData<typeof answered>(["chat", 2])?.messages.map((m) => m.id)).toEqual([10, 11]);
  unsubscribe();
});

test("a conversation that already holds the answer is read once", async () => {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: 0 } } });
  const queryFn = jest.fn().mockResolvedValue(answered);
  const observer = new QueryObserver(qc, { queryKey: ["chat", 2], queryFn });
  const unsubscribe = observer.subscribe(() => {});
  await qc.getQueryCache().find({ queryKey: ["chat", 2] })?.promise;
  await refreshAnswered(qc, 2, 11);
  expect(queryFn).toHaveBeenCalledTimes(2); // the load, then the one refetch
  unsubscribe();
});
