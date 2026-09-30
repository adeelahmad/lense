import { Aperture } from "lucide-react";
import Link from "next/link";

import { cn } from "@/lib/utils";

/** The product mark. Placeholder until the design handoff supplies a logo. */
export function Brand({
  className,
  href = "/",
}: {
  className?: string;
  href?: string;
}) {
  return (
    <Link
      href={href}
      className={cn(
        "inline-flex items-center gap-2 font-semibold tracking-tight",
        className,
      )}
    >
      <Aperture className="h-5 w-5" aria-hidden />
      <span>Lens</span>
    </Link>
  );
}
