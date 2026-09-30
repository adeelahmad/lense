/** Signed-out pages: a centred card on the app surface. */
export default function AuthLayout({ children }: { children: React.ReactNode }) {
  return (
    <main className="flex min-h-screen flex-col items-center justify-center bg-surface px-4 py-12">
      <div className="w-full max-w-[400px]">{children}</div>
    </main>
  );
}
