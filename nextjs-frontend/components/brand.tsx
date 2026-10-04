import Link from "next/link";

import { LensMark } from "@/components/ui/lens-mark";
import { cn } from "@/lib/utils";

/**
 * The Lens lockup from the brand kit: the Search Lens mark beside "Lens" in DM Sans, ink on light and
 * white on dark. Collapsed, it is the mark alone.
 */
export function Brand({
  className,
  href = "/",
  collapsed,
  size = 18,
}: {
  className?: string;
  href?: string;
  collapsed?: boolean;
  size?: number;
}) {
  return (
    <Link href={href} aria-label="Lens home" className={cn("inline-flex items-center", className)}>
      <span className="inline-flex items-center" style={{ gap: Math.round(size * 0.4) }}>
        <LensMark size={Math.round(size * 1.35)} />
        {!collapsed && (
          <span
            className="whitespace-nowrap font-bold leading-none tracking-[-0.02em] text-fg"
            style={{ fontSize: size * 1.1 }}
          >
            Lens
          </span>
        )}
      </span>
    </Link>
  );
}
