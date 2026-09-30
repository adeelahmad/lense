import type { Metadata } from "next";

import { AuthBrand } from "@/components/auth/auth-card";
import { NotFoundState } from "@/components/errors/error-states";
import { TooltipProvider } from "@/components/ui/tooltip";

export const metadata: Metadata = { title: "Not found" };

/** Unknown pages outside the app shell (Access AC6). */
export default function NotFound() {
  return (
    <TooltipProvider>
      <main className="flex min-h-screen flex-col items-center justify-center gap-6 bg-surface px-4 py-12">
        <AuthBrand />
        <div className="w-full max-w-[520px] rounded-md border border-border bg-background">
          <NotFoundState />
        </div>
      </main>
    </TooltipProvider>
  );
}
