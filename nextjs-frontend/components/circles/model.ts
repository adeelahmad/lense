/** Circles of trust (docs/circles.md): who a person is, who can still be added to a circle, and what a share says it
 * goes to. No React here, so it is tested on its own. */

import type { Circle, Person } from "@/app/openapi-client/types.gen";

/** A person's name, else their email, else their account number. */
export function personLabel(p: Pick<Person, "id" | "name" | "email">): string {
  return p.name?.trim() || p.email || `Account ${p.id}`;
}

/** The people who can still be added to a circle, by name. */
export function addable(people: Person[], circle: Pick<Circle, "members">): Person[] {
  const inside = new Set(circle.members.map((m) => m.id));
  return people.filter((p) => !inside.has(p.id)).sort((a, b) => personLabel(a).localeCompare(personLabel(b)));
}

/** Who a share goes to, in words: "Immediate family", "Immediate family and Friends", "3 circles". */
export function whom(circles: Pick<Circle, "id" | "name">[], picked: number[]): string {
  const names = circles.filter((c) => picked.includes(c.id)).map((c) => c.name);
  if (names.length === 0) return "";
  if (names.length === 1) return names[0];
  if (names.length === 2) return `${names[0]} and ${names[1]}`;
  return `${names.length} circles`;
}

/** How many different people a share reaches: everyone in the picked circles, once each. */
export function reach(circles: Pick<Circle, "id" | "members">[], picked: number[]): number {
  const ids = new Set<number>();
  for (const c of circles) if (picked.includes(c.id)) for (const m of c.members) ids.add(m.id);
  return ids.size;
}

/** The circles a share starts with: the only circle when there is one, else none. */
export function firstPick(circles: Pick<Circle, "id">[]): number[] {
  return circles.length === 1 ? [circles[0].id] : [];
}

/** Whether an activity can be shared: shares themselves are not passed on. */
export function shareable(kind: string): boolean {
  return !kind.startsWith("share.");
}

export type Let = "free_busy" | "questions";

/** A circle's `lets` with one turned on or off, keeping the others. */
export function withLet(lets: Let[] | undefined, which: Let, on: boolean): Let[] {
  const rest = (lets ?? []).filter((l) => l !== which);
  return on ? [...rest, which] : rest;
}
