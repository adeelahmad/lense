"use client";

import { Inbox } from "lucide-react";
import Link from "next/link";

import { badgeCount } from "@/components/approvals/model";
import { useApprovalCount } from "@/components/approvals/use-approvals";

/** The top bar's badge: how many approvals wait for your pick, opening the inbox. Nothing shows while none wait. */
export function ApprovalsBadge() {
  const count = useApprovalCount();
  const n = count.data?.waiting ?? 0;
  const label = badgeCount(n);
  if (!label) return null;
  return (
    <Link
      href="/approvals"
      aria-label={`${n} waiting for your approval`}
      title="Approvals"
      className="relative inline-grid size-9 shrink-0 place-items-center rounded-full text-fg-secondary transition-colors duration-fast hover:bg-surface-neutral [&_svg]:size-[18px]"
    >
      <Inbox />
      <span
        aria-hidden
        className="absolute -right-0.5 -top-0.5 min-w-[18px] rounded-pill bg-gold px-1 text-center text-[11px] font-bold leading-[18px] text-fg"
      >
        {label}
      </span>
    </Link>
  );
}
