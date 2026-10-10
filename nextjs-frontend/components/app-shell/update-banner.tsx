"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { useUpdates } from "@/components/settings/updates-panel";
import { Banner } from "@/components/ui/banner";

const KEY = "lens-update-dismissed";

/** For admins: a new release is out (until dismissed for that release), or one is being installed. */
export function UpdateBanner({ admin }: { admin: boolean }) {
  const { data: s } = useUpdates(admin);
  const [dismissed, setDismissed] = useState<string | null>(null);
  useEffect(() => {
    try {
      setDismissed(localStorage.getItem(KEY));
    } catch {
      // private window: shown again next time
    }
  }, []);
  if (!admin || !s) return null;
  const run = s.upgrade;
  if (run && ["backing-up", "queued", "running"].includes(run.state) && !run.error)
    return (
      <Banner className="mx-4 mt-3 md:mx-6" title={`Installing Lens ${run.ref}…`}>
        Lens restarts on its own when it’s done.{" "}
        <Link href="/settings/updates" className="underline">
          Follow along
        </Link>
      </Banner>
    );
  const tag = s.latest?.tag;
  if (!s.available || !tag || dismissed === tag) return null;
  return (
    <Banner
      className="mx-4 mt-3 md:mx-6"
      title={`Lens ${tag} is out (this is ${s.current}).`}
      onDismiss={() => {
        setDismissed(tag);
        try {
          localStorage.setItem(KEY, tag);
        } catch {
          // fine: dismissed for this visit
        }
      }}
    >
      <Link href="/settings/updates" className="underline">
        {s.agent ? "See what’s new and install it" : "See what’s new"}
      </Link>
    </Banner>
  );
}
