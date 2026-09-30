"use client";

import { useQuery } from "@tanstack/react-query";
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";

import { Auth, Namespaces } from "@/app/openapi-client";
import type { Me, Namespace } from "@/app/openapi-client/types.gen";
import { data, useApiClient } from "@/lib/api/browser";

export type Role = "viewer" | "editor" | "owner";
const RANK: Record<Role, number> = { viewer: 1, editor: 2, owner: 3 };

type Ctx = {
  me: Me | undefined;
  namespaces: Namespace[];
  /** The namespace picked in the top bar; null means all namespaces. */
  namespace: string | null;
  setNamespace: (name: string | null) => void;
  /** The person's role in a namespace (admins own everything); undefined when they have none. */
  roleIn: (ns: string | null | undefined) => Role | undefined;
  /** Whether they have at least `role` in `ns` (or, with ns omitted, in any namespace). */
  can: (role: Role, ns?: string | null) => boolean;
  admin: boolean;
};

const ArchiveCtx = createContext<Ctx | null>(null);
const KEY = "lens.namespace";

/** Who is signed in, their roles, and the current namespace. Every screen reads this for the role rule. */
export function ArchiveProvider({ children }: { children: ReactNode }) {
  const client = useApiClient();
  const me = useQuery({
    queryKey: ["me"],
    queryFn: () => data(Auth.me({ client })),
    staleTime: 60_000,
  });
  const ns = useQuery({
    queryKey: ["namespaces"],
    queryFn: () => data(Namespaces.listNamespaces({ client })),
    staleTime: 30_000,
  });
  const [namespace, setNs] = useState<string | null>(null);

  useEffect(() => {
    try {
      setNs(localStorage.getItem(KEY) || null);
    } catch {
      /* storage unavailable */
    }
  }, []);
  // Forget a namespace that no longer exists or that the person lost access to.
  useEffect(() => {
    if (namespace && ns.data && !ns.data.some((n) => n.name === namespace)) setNs(null);
  }, [namespace, ns.data]);

  const setNamespace = useCallback((name: string | null) => {
    setNs(name);
    try {
      if (name) localStorage.setItem(KEY, name);
      else localStorage.removeItem(KEY);
    } catch {
      /* storage unavailable */
    }
  }, []);

  const value = useMemo<Ctx>(() => {
    const roles = (me.data?.roles ?? {}) as Record<string, Role>;
    const admin = Boolean(me.data?.user.admin);
    const roleIn = (n: string | null | undefined) => (n ? (admin ? "owner" : roles[n]) : undefined);
    const can = (role: Role, n?: string | null) =>
      admin || (n ? RANK[roles[n] as Role] >= RANK[role] : Object.values(roles).some((r) => RANK[r] >= RANK[role]));
    return {
      me: me.data,
      namespaces: ns.data ?? [],
      namespace,
      setNamespace,
      roleIn,
      can,
      admin,
    };
  }, [me.data, ns.data, namespace, setNamespace]);

  return <ArchiveCtx.Provider value={value}>{children}</ArchiveCtx.Provider>;
}

export function useArchive(): Ctx {
  const c = useContext(ArchiveCtx);
  if (!c) throw new Error("useArchive needs ArchiveProvider");
  return c;
}

/** "Only owners of podcasts can merge speakers": the reason shown on a disabled action. */
export function needRole(role: Role, ns?: string | null): string {
  const who = role === "viewer" ? "People with access to" : `${role[0].toUpperCase()}${role.slice(1)}s of`;
  return ns ? `${who} ${ns} can do this` : `Needs ${role} access`;
}
