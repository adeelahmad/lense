/**
 * Signed out mid-task (Access AC3). When an API call comes back 401, the request is held here while the
 * "You've been signed out" dialog asks for the password again; after signing in, every held request is replayed
 * with the new access token, so the page keeps its place (scroll, selection, form input).
 *
 * It's a tiny store outside React: the API client's fetch (lib/api/browser.ts) holds requests, and the dialog
 * (components/auth/signed-out-dialog.tsx, rendered by the session guard) subscribes to it.
 */

export type HeldRequest = { method: string; url: string };
export type ReauthState = { open: boolean; held: HeldRequest[] };

/** Rejects the held requests when someone chooses to sign in as another person. */
export class SignedOutError extends Error {
  constructor() {
    super("Signed out");
    this.name = "SignedOutError";
  }
}

type Waiter = {
  promise: Promise<string>;
  resolve: (token: string) => void;
  reject: (e: Error) => void;
};

let state: ReauthState = { open: false, held: [] };
let waiter: Waiter | null = null;
const listeners = new Set<() => void>();
let refresher: (() => Promise<string | null>) | null = null;

function emit() {
  for (const fn of listeners) fn();
}

function ensureWaiter(): Waiter {
  if (!waiter) {
    let resolve!: (token: string) => void;
    let reject!: (e: Error) => void;
    const promise = new Promise<string>((res, rej) => {
      resolve = res;
      reject = rej;
    });
    // Nobody may be awaiting it (the dialog opened on its own); don't report that as unhandled.
    promise.catch(() => undefined);
    waiter = { promise, resolve, reject };
  }
  return waiter;
}

export function subscribe(fn: () => void): () => void {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

export function getReauthState(): ReauthState {
  return state;
}

/** The session guard registers how to fetch a fresh session (a silent refresh), returning its access token. */
export function setSessionRefresher(fn: (() => Promise<string | null>) | null) {
  refresher = fn;
}

/** A newer access token than `current`, if the session can still be refreshed without asking for the password. */
export async function refreshedToken(current: string | null): Promise<string | null> {
  if (!refresher) return null;
  try {
    const token = await refresher();
    return token && `Bearer ${token}` !== current ? token : null;
  } catch {
    return null;
  }
}

/** Holds a request that came back 401. Resolves with the new access token once the person has signed in again. */
export function holdUntilSignedIn(req: HeldRequest): Promise<string> {
  const w = ensureWaiter();
  state = { open: true, held: [...state.held, req] };
  emit();
  return w.promise;
}

/** Opens the dialog without a held request (the session guard saw the session end). */
export function openSignedOut() {
  ensureWaiter();
  if (!state.open) {
    state = { ...state, open: true };
    emit();
  }
}

/** Signed in again as the same person: replay everything that was held. */
export function signedInAgain(token: string) {
  waiter?.resolve(token);
  waiter = null;
  state = { open: false, held: [] };
  emit();
}

/** Signing in as someone else discards what was held. */
export function discardHeld() {
  waiter?.reject(new SignedOutError());
  waiter = null;
  state = { open: false, held: [] };
  emit();
}

const WRITE_LABELS: [RegExp, string][] = [
  [/\/settings\//, "saving settings"],
  [/\/metadata(\/|$)/, "saving the metadata"],
  [/\/tokens(\/|$)/, "your API token change"],
  [/\/members(\/|$)/, "saving roles"],
  [/\/users(\/|$)/, "saving the account change"],
  [/\/import/, "the import"],
  [/\/jobs/, "queueing the work"],
  [/\/(chats|messages)/, "sending your message"],
  [/\/speakers/, "the speaker change"],
  [/\/entities/, "the entity change"],
  [/\/collections/, "saving the collection"],
  [/\/share/, "the share link"],
];

/** What the held requests were doing, for the dialog: "saving settings", or null when it was only loading. */
export function describeHeld(held: HeldRequest[]): string | null {
  const writes = held.filter((r) => !["GET", "HEAD", "OPTIONS"].includes(r.method.toUpperCase()));
  if (!writes.length) return null;
  const labels = new Set(
    writes.map((r) => {
      let path = r.url;
      try {
        path = new URL(r.url, "http://x").pathname;
      } catch {
        /* keep the raw url */
      }
      return WRITE_LABELS.find(([rx]) => rx.test(path))?.[1] ?? "saving your changes";
    }),
  );
  return labels.size === 1 ? [...labels][0] : "saving your changes";
}
