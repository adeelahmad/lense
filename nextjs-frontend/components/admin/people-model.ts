/**
 * People (Admin AD1–AD2): pending changes in the role matrix, and temporary passwords that are easy to read out.
 */
export type Role = "viewer" | "editor" | "owner";
export type Person = { id: number; email: string; name?: string | null; admin?: boolean; disabled?: boolean | null; last_login_at?: string | null; roles?: Record<string, Role> };

/** One cell changed: a role in a namespace (null removes it), or the platform admin switch. */
export type Pending = { uid: number; ns: string; role: Role | null } | { uid: number; admin: boolean };
export const cellKey = (uid: number, col: string) => `${uid}:${col}`;

export function roleLabel(r: Role | null | undefined): string {
  return r ? r[0].toUpperCase() + r.slice(1) : "—";
}

/** "Sam Whitaker · customer-calls — → Viewer", "Lena Vogel · platform admin off → on". */
export function describePending(p: Pending, people: Person[]): string {
  const who = people.find((x) => x.id === p.uid);
  const name = who?.name || who?.email || `Account ${p.uid}`;
  if ("admin" in p) return `${name} · platform admin ${p.admin ? "off → on" : "on → off"}`;
  return `${name} · ${p.ns} ${roleLabel(who?.roles?.[p.ns])} → ${roleLabel(p.role)}`;
}

/** Applies a cell edit to the pending map: back to the saved value removes it. */
export function withPending(pending: Map<string, Pending>, people: Person[], next: Pending): Map<string, Pending> {
  const out = new Map(pending);
  const who = people.find((x) => x.id === next.uid);
  if ("admin" in next) {
    const key = cellKey(next.uid, "admin");
    if (Boolean(who?.admin) === next.admin) out.delete(key);
    else out.set(key, next);
  } else {
    const key = cellKey(next.uid, next.ns);
    if ((who?.roles?.[next.ns] ?? null) === next.role) out.delete(key);
    else out.set(key, next);
  }
  return out;
}

const WORDS =
  "amber anchor apple arrow aspen basil beacon birch bramble breeze canyon cedar chalk cinder clover comet copper coral crane crystal dahlia delta ember fennel fern fjord flint forest garnet glacier granite harbor hazel heron indigo iris ivory jasper juniper kelp kestrel lantern larch lemon lilac linen lotus maple marble meadow mint moss nectar nickel oak ochre olive onyx orbit orchid otter pebble pepper pine plaster plum poppy prairie quartz quill raven reed ripple river saffron sage sequoia shale silver slate sorrel spruce stone summit tide timber topaz tulip umber valley velvet willow";

/** A temporary password like "tide-copper-41-lantern": three words and a number (well over 10 characters). */
export function tempPassword(random: (n: number) => number = cryptoRandom): string {
  const words = WORDS.split(" ");
  const pick = () => words[random(words.length)];
  return `${pick()}-${pick()}-${10 + random(90)}-${pick()}`;
}

function cryptoRandom(n: number): number {
  const a = new Uint32Array(1);
  crypto.getRandomValues(a);
  return a[0] % n;
}
