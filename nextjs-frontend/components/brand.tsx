import Link from "next/link";

import { cn } from "@/lib/utils";

/**
 * The Lens mark: an AI looking through a lens. A head of one colour with ring eyes and a spark beside it, and a
 * magnifying glass in front of one eye, which shows larger behind the glass. The four brand colours, the same in
 * light and dark: blue head, gold rim, green handle, red spark.
 */
export function LensMark({ size = 24, className }: { size?: number; className?: string }) {
  return (
    <svg aria-hidden viewBox="0 0 48 48" width={size} height={size} fill="none" className={cn("shrink-0", className)}>
      <circle cx="29.5" cy="23" r="15" fill="var(--aladdin-blue)" />
      <circle cx="36.2" cy="21.5" r="3.3" stroke="var(--text-on-color)" strokeWidth="2.3" />
      <path d="M27.5 31.2c2 1.5 4.6 1.6 6.8.3" stroke="var(--text-on-color)" strokeWidth="2" strokeLinecap="round" />
      <path
        d="M42.5 2.5c.5 2.7 1.3 3.5 4 4-2.7.5-3.5 1.3-4 4-.5-2.7-1.3-3.5-4-4 2.7-.5 3.5-1.3 4-4Z"
        fill="var(--aladdin-red)"
      />
      <path d="M9.6 31.4 4.4 39.2" stroke="var(--aladdin-green)" strokeWidth="4.6" strokeLinecap="round" />
      <circle cx="17" cy="21" r="11.6" fill="var(--text-on-color)" />
      <circle
        cx="17"
        cy="21"
        r="11.6"
        fill="var(--aladdin-blue)"
        fillOpacity="0.1"
        stroke="var(--aladdin-gold)"
        strokeWidth="3.4"
      />
      <circle cx="19.4" cy="21" r="5" stroke="var(--aladdin-blue)" strokeWidth="3" />
    </svg>
  );
}

/** The mark and the wordmark, "Lens": DM Sans 800, -0.03em, blue. Collapsed, the mark alone. */
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
    <Link href={href} aria-label="Lens home" className={cn("inline-flex items-center gap-2", className)}>
      <LensMark size={Math.round(size * 1.8)} />
      {!collapsed && (
        <span
          className="whitespace-nowrap font-extrabold leading-none tracking-[-0.03em] text-blue"
          style={{ fontSize: size }}
        >
          Lens
        </span>
      )}
    </Link>
  );
}
