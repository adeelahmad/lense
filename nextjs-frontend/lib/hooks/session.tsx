"use client";

import { useQuery } from "@tanstack/react-query";
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";

import { Auth, Namespaces } from "@/app/openapi-client";
import type { Me, Namespace } from "@/app/openapi-client/types.gen";
import { data, useApiClient } from "@/lib/api/browser";

import { RANK, type Role } from "@/lib/roles";

export type { Role } from "@/lib/roles";

type Ctx = {
  me: Me | undefined;
  /** The namespaces the person has a role in (admins: all of them). */
  namespaces: Namespace[];
  /** The namespaces they see only some collections of (roles on collections): the Library, search and those
   * recordings, but none of the namespace-wide pages. */
  partialNamespaces: Namespace[];
  /** Whether they see only some collections of `ns`. */
  isPartial: (ns: string | null | undefined) => boolean;
  /** Who they are and the namespaces are known (until then roles and isPartial say no). */
  loaded: boolean;
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

/** The one namespace there is, if there is just one: it's the current namespace until another is picked, so pages
 * that need one don't ask first. */
export function onlyNamespace(all: { name: string; partial?: boolean | null }[]): string | null {
  return all.length === 1 && !all[0].partial ? all[0].name : null;
}

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
    const all = ns.data ?? [];
    const partialNamespaces = all.filter((n) => n.partial);
    return {
      me: me.data,
      namespaces: all.filter((n) => !n.partial),
      partialNamespaces,
      isPartial: (n: string | null | undefined) => Boolean(n && partialNamespaces.some((p) => p.name === n)),
      loaded: me.isSuccess && ns.isSuccess,
      namespace: namespace ?? onlyNamespace(all),
      setNamespace,
      roleIn,
      can,
      admin,
    };
  }, [me.data, me.isSuccess, ns.data, ns.isSuccess, namespace, setNamespace]);

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
