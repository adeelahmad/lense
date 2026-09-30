import { TooltipProvider } from "@/components/ui/tooltip";

/** Signed-out pages: one card centred on the app surface (Access AC1–AC2). */
export default function AuthLayout({ children }: { children: React.ReactNode }) {
  return (
    <TooltipProvider delayDuration={250}>
      <main className="flex min-h-screen flex-col items-center justify-center bg-surface px-4 py-12">
        <div className="w-full max-w-[440px]">{children}</div>
      </main>
    </TooltipProvider>
  );
}
