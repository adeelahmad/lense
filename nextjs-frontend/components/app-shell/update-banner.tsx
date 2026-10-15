"use client";

import Link from "next/link";
import { type ReactNode, useEffect, useRef, useState } from "react";

import { useUpdates } from "@/components/settings/updates-panel";
import { Banner } from "@/components/ui/banner";

const KEY = "lens-update-dismissed";

/** For admins: a new release is out (until dismissed for that release), or one is being installed. */
export function UpdateBanner({ admin }: { admin: boolean }) {
  const { data: s } = useUpdates(admin);
  const [dismissed, setDismissed] = useState<string | null>(null);
  const room = useRef<HTMLDivElement>(null);
  useEffect(() => {
    try {
      setDismissed(localStorage.getItem(KEY));
    } catch {
      // private window: shown again next time
    }
  }, []);

  let banner: ReactNode = null;
  const run = s?.upgrade;
  const tag = s?.latest?.tag;
  if (!admin || !s) banner = null;
  else if (run && ["backing-up", "queued", "running"].includes(run.state) && !run.error)
    banner = (
      <Banner title={`Installing Lens ${run.ref}…`}>
        Lens restarts on its own when it’s done.{" "}
        <Link href="/settings/updates" className="underline">
          Follow along
        </Link>
      </Banner>
    );
  else if (s.available && tag && dismissed !== tag)
    banner = (
      <Banner
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

  // Pages that fill the screen (Chat, the graph, a recording) leave the banner its room: --lens-banner is its height
  const shown = banner != null;
  useEffect(() => {
    const root = document.documentElement;
    const el = room.current;
    if (!shown || !el) {
      root.style.removeProperty("--lens-banner");
      return;
    }
    const set = () => root.style.setProperty("--lens-banner", `${el.offsetHeight}px`);
    set();
    const watch = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(set);
    watch?.observe(el);
    return () => {
      watch?.disconnect();
      root.style.removeProperty("--lens-banner");
    };
  }, [shown]);

  if (!banner) return null;
  return (
    <div ref={room} className="px-4 pt-3 md:px-6">
      {banner}
    </div>
  );
}
