"use client";

import { useQuery } from "@tanstack/react-query";
import { Shield } from "lucide-react";
import Link from "next/link";
import type { ReactNode } from "react";

import { Users } from "@/app/openapi-client";
import { Button } from "@/components/ui/button";
import { EmptyState, PageHeader } from "@/components/ui/states";
import { Tabs } from "@/components/ui/tabs";
import { data, useApiClient } from "@/lib/api/browser";
import { useArchive } from "@/lib/hooks/session";

export type AdminTab = "people" | "namespaces" | "audit" | "health";

/** Everyone, for admins (the role matrix, audit names). */
export function usePeople() {
  const client = useApiClient();
  const { admin } = useArchive();
  return useQuery({
    queryKey: ["users"],
    queryFn: () => data(Users.listUsers({ client })),
    enabled: admin,
    staleTime: 15_000,
  });
}

/**
 * Admin is one sub-nav (Admin AD1–AD5): People, Namespaces, Audit log, System health. Namespace owners who aren't
 * admins see only their namespaces' members.
 */
export function AdminFrame({
  tab,
  title,
  meta,
  actions,
  children,
  ownersToo,
}: {
  tab: AdminTab;
  title: ReactNode;
  meta?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  ownersToo?: boolean;
}) {
  const { admin, me, can } = useArchive();
  const people = usePeople();
  const owner = can("owner");
  if (me && !admin && !(ownersToo && owner))
    return (
      <div className="px-4 py-5 sm:px-6">
        <EmptyState
          icon={<Shield />}
          title="Admin is for platform admins"
          actions={
            <Button asChild>
              <Link href="/">Go to Home</Link>
            </Button>
          }
        >
          {owner ? (
            <>
              As an owner you manage members from{" "}
              <Link className="font-semibold text-fg-accent underline" href="/admin/namespaces">
                your namespaces
              </Link>
              .
            </>
          ) : (
            "Admins manage accounts, roles, the audit log and system health."
          )}
        </EmptyState>
      </div>
    );
  const items = admin
    ? [
        {
          value: "people",
          label: "People",
          count: people.data?.length,
          href: "/admin/people",
        },
        { value: "namespaces", label: "Namespaces", href: "/admin/namespaces" },
        { value: "audit", label: "Audit log", href: "/admin/audit" },
        { value: "health", label: "System health", href: "/admin/health" },
      ]
    : [
        {
          value: "namespaces",
          label: "Your namespaces",
          href: "/admin/namespaces",
        },
      ];
  return (
    <div className="flex flex-col gap-3.5 px-4 py-[18px] sm:px-7">
      <PageHeader title={title} meta={meta} actions={actions} className="mb-0" />
      <Tabs items={items} value={tab} aria-label="Admin" />
      {children}
    </div>
  );
}
