/** Roles, as the API names them: in a namespace (viewer, editor, owner), and on a recording, where a role on its
 * collection counts too (an admin of the collection is an owner of its recordings). */
export type Role = "viewer" | "editor" | "owner";

export const RANK: Record<Role, number> = { viewer: 1, editor: 2, owner: 3 };

/** Whether `role` allows what `need` allows. */
export function atLeast(role: string | null | undefined, need: Role): boolean {
  return (RANK[role as Role] ?? 0) >= RANK[need];
}
