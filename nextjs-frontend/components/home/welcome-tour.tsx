"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ChartNoAxesColumn, Sparkles } from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useState, type ReactNode } from "react";

import { Auth } from "@/app/openapi-client";
import type { Me } from "@/app/openapi-client/types.gen";
import { assistantChatHref } from "@/components/home/assistant-home";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { Input } from "@/components/ui/field";
import { data, useApiClient } from "@/lib/api/browser";
import { useArchive } from "@/lib/hooks/session";

/** The tour opens by itself for someone signed in to the web app who hasn't finished or skipped it yet. */
export function tourDue(me: Pick<Me, "toured_at" | "via"> | undefined): boolean {
  return Boolean(me) && me?.via === "access" && !me?.toured_at;
}

function Stop({ icon, title, children }: { icon: ReactNode; title: string; children: ReactNode }) {
  return (
    <section className="flex flex-col gap-3 rounded-lg border border-border p-4">
      <div className="flex items-center gap-2.5">
        <span className="grid size-8 shrink-0 place-items-center rounded-full bg-blue-surface text-blue [&_svg]:size-4">
          {icon}
        </span>
        <h3 className="text-[15.5px] font-bold text-fg">{title}</h3>
      </div>
      {children}
    </section>
  );
}

/**
 * After someone's first sign-in: the two places people come back for, the assistant and the charts, each one step away.
 * Asking, opening Reports, skipping or closing all count as seen, so it opens once; the account menu opens it again.
 */
export function WelcomeTour() {
  const { me } = useArchive();
  const client = useApiClient();
  const queries = useQueryClient();
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const due = tourDue(me);

  useEffect(() => {
    if (due) setOpen(true);
  }, [due]);
  useEffect(() => {
    const replay = () => setOpen(true);
    window.addEventListener("lens:tour", replay);
    return () => window.removeEventListener("lens:tour", replay);
  }, []);

  const seen = useMutation({
    mutationFn: () => data(Auth.finishTour({ client })),
    onSuccess: (got) => queries.setQueryData(["me"], got),
  });
  const close = (href?: string) => {
    setOpen(false);
    if (due && !seen.isPending) seen.mutate();
    if (href) router.push(href);
  };

  return (
    <Dialog
      open={open}
      onOpenChange={(o) => (o ? setOpen(true) : close())}
      title="Welcome to Lens"
      description="Most people start in one of these two places. Open this again any time from the account menu."
      actions={
        <Button variant="ghost" onClick={() => close()}>
          Skip
        </Button>
      }
    >
      <Stop icon={<Sparkles />} title="Ask the assistant">
        <p className="text-[14px] leading-normal text-fg-secondary">
          It answers from everything you have added and shows the source of each answer.
        </p>
        <form
          className="flex gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            close(assistantChatHref({ q: q.trim() || undefined }));
          }}
        >
          <Input
            data-autofocus
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Ask anything"
            aria-label="Ask the assistant"
            className="min-w-0 flex-1"
          />
          <Button type="submit" variant="primary">
            Ask
          </Button>
        </form>
      </Stop>
      <Stop icon={<ChartNoAxesColumn />} title="See your charts">
        <p className="text-[14px] leading-normal text-fg-secondary">
          Reports turn what you have added into charts, per namespace and per recording.
        </p>
        <div>
          <Button variant="secondary" onClick={() => close("/reports")}>
            Open Reports
          </Button>
        </div>
      </Stop>
    </Dialog>
  );
}
