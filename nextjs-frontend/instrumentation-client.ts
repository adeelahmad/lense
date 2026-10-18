// Next runs this in the browser before the app starts (docs: instrumentation-client).
import { finishPageChangesOnHistoryWrites, routeStarted } from "@/lib/progress";

finishPageChangesOnHistoryWrites();

/** Starts the loading bar on every client-side page change; it finishes once Next writes the new address. */
export function onRouterTransitionStart(url: string) {
  routeStarted(url);
}
