import Link from "next/link";

import { Signature } from "@/components/ui/loop";
import { cn } from "@/lib/utils";

/**
 * The Lens Archive wordmark: DM Sans 800, -0.03em, blue, over a four-segment loop bar
 * (blue → red → green → gold). Collapsed, it becomes the four-dot signature.
 */
export function Brand({ className, href = "/", collapsed, size = 18 }: { className?: string; href?: string; collapsed?: boolean; size?: number }) {
  return (
    <Link href={href} aria-label="Lens Archive home" className={cn("inline-flex items-center", className)}>
      {collapsed ? (
        <span className="px-1.5 py-0.5">
          <Signature grid size={8} gap={4} />
        </span>
      ) : (
        <span className="inline-flex flex-col gap-1">
          <span className="whitespace-nowrap font-extrabold leading-none tracking-[-0.03em] text-blue" style={{ fontSize: size }}>
            Lens Archive
          </span>
          <span aria-hidden className="flex h-[3px] overflow-hidden rounded-pill">
            {["var(--aladdin-blue)", "var(--aladdin-red)", "var(--aladdin-green)", "var(--aladdin-gold)"].map((c) => (
              <span key={c} className="flex-1" style={{ background: c }} />
            ))}
          </span>
        </span>
      )}
    </Link>
  );
}
