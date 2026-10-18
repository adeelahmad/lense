import { QueryClient, QueryClientProvider, useQuery } from "@tanstack/react-query";
import { act, render, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom";
import NProgress from "nprogress";

import { DataProgress } from "@/components/top-progress";
import { onRouterTransitionStart } from "@/instrumentation-client";
import { SHOW_AFTER_MS, setLoading } from "@/lib/progress";

const pause = (ms: number) => act(() => new Promise((r) => setTimeout(r, ms)));
const deferred = () => {
  let resolve!: (v: string) => void;
  const promise = new Promise<string>((r) => (resolve = r));
  return { promise, resolve };
};

function Page({ fetch }: { fetch: () => Promise<string> }) {
  const { data } = useQuery({ queryKey: ["page"], queryFn: fetch });
  return <p>{data}</p>;
}

function withData(fetch: () => Promise<string>, qc = new QueryClient()) {
  return render(
    <QueryClientProvider client={qc}>
      <DataProgress />
      <Page fetch={fetch} />
    </QueryClientProvider>,
  );
}

afterEach(() => {
  setLoading("route", false);
  setLoading("data", false);
  window.history.replaceState(null, "", "/");
});

describe("the loading bar", () => {
  it("runs along the top while a page's first data loads", async () => {
    const answer = deferred();
    withData(() => answer.promise);
    expect(NProgress.isStarted()).toBe(false); // not for the first blink
    await waitFor(() => expect(NProgress.isStarted()).toBe(true));
    expect(document.querySelector("#nprogress .bar")).toBeInTheDocument();
    await act(async () => answer.resolve("ready"));
    await waitFor(() => expect(NProgress.isStarted()).toBe(false));
  });

  it("doesn't flash for a quick answer, or show for refreshing what's on screen", async () => {
    const qc = new QueryClient();
    let fetch = () => Promise.resolve("ready");
    withData(() => fetch(), qc);
    await pause(SHOW_AFTER_MS * 2);
    expect(NProgress.isStarted()).toBe(false);

    const slow = deferred();
    fetch = () => slow.promise;
    act(() => void qc.refetchQueries({ queryKey: ["page"] }));
    await pause(SHOW_AFTER_MS * 2);
    expect(qc.isFetching()).toBe(1);
    expect(NProgress.isStarted()).toBe(false);
    await act(async () => slow.resolve("again"));
  });

  it("runs from a page change until Next writes the new address", async () => {
    onRouterTransitionStart("/library");
    await waitFor(() => expect(NProgress.isStarted()).toBe(true));
    window.history.pushState(null, "", "/library");
    expect(NProgress.isStarted()).toBe(false);
  });

  it("finishes a page change that lands back on the page showing, as a redirect can", async () => {
    window.history.replaceState(null, "", "/admin/people");
    onRouterTransitionStart("/admin");
    await waitFor(() => expect(NProgress.isStarted()).toBe(true));
    window.history.replaceState(null, "", "/admin/people");
    expect(NProgress.isStarted()).toBe(false);
  });

  it("stays off going to the page already showing, an anchor on it, or another site", async () => {
    for (const url of ["/", "/#notes", "http://localhost/", "https://example.org/library"])
      onRouterTransitionStart(url);
    await pause(SHOW_AFTER_MS * 2);
    expect(NProgress.isStarted()).toBe(false);
  });
});
