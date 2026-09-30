import Link from "next/link";

export default function NotFound() {
  return (
    <main className="flex min-h-screen flex-col items-center justify-center gap-2 px-4 text-center">
      <h1 className="text-2xl font-semibold tracking-tight">Page not found</h1>
      <Link
        href="/"
        className="text-sm font-medium underline underline-offset-4"
      >
        Go to Lens
      </Link>
    </main>
  );
}
