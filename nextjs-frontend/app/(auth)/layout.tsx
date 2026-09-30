import { Brand } from "@/components/brand";

/** Signed-out pages: a centred card under the product mark. */
export default function AuthLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <main className="flex min-h-screen flex-col items-center justify-center gap-6 bg-muted/40 px-4 py-12">
      <Brand className="text-xl" />
      <div className="w-full max-w-sm">{children}</div>
    </main>
  );
}
