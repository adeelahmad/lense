import NProgress from "nprogress";

/** Loading shorter than this never shows the bar, so a cached page or a quick answer doesn't flash it. */
export const SHOW_AFTER_MS = 150;

NProgress.configure({
  showSpinner: false,
  minimum: 0.1,
  trickleSpeed: 200,
  barSelector: ".bar",
  template: '<div class="bar"><div class="peg"></div></div>',
});

// What is loading right now: "route" (a page change) and "data" (a page's first answers from the API).
const loading = new Set<string>();
let timer: ReturnType<typeof setTimeout> | undefined;

/** The thin bar along the top of the window, as in Gmail: it runs while anything is loading and finishes when nothing
 * is. */
export function setLoading(what: string, busy: boolean) {
  if (busy) loading.add(what);
  else loading.delete(what);
  if (loading.size) {
    if (timer === undefined && !NProgress.isStarted()) {
      timer = setTimeout(() => {
        timer = undefined;
        NProgress.start();
      }, SHOW_AFTER_MS);
    }
  } else {
    clearTimeout(timer);
    timer = undefined;
    NProgress.done();
  }
}

/** A page change has started (Next's onRouterTransitionStart: a link, router.push or back and forward). Going to the
 * page already showing, or only to an anchor on it, loads nothing. */
export function routeStarted(href: string) {
  const to = new URL(href, window.location.href);
  const here = window.location;
  setLoading("route", to.origin === here.origin && to.pathname + to.search !== here.pathname + here.search);
}

/** Next writes the address into the browser's history as each page change lands, even one that ends on the page already
 * showing (a redirect back to it), so that write is when the page change is done. */
export function finishPageChangesOnHistoryWrites() {
  for (const method of ["pushState", "replaceState"] as const) {
    const write = window.history[method];
    window.history[method] = function (this: History, ...args: Parameters<History["pushState"]>) {
      setLoading("route", false);
      return write.apply(this, args);
    };
  }
}
