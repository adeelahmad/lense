import Link from "next/link";

export default function AppNotFound() {
  return (
    <div className="grid max-w-md gap-2">
      <h1 className="text-2xl font-semibold tracking-tight">
        Nothing here yet
      </h1>
      <p className="text-sm text-fg-secondary">
        This page doesn&apos;t exist, or this part of Lens hasn&apos;t been
        built yet.
      </p>
      <Link
        href="/"
        className="text-sm font-medium underline underline-offset-4"
      >
        Back to the overview
      </Link>
    </div>
  );
}
