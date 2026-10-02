"use client";

import { useQuery } from "@tanstack/react-query";
import {
  AudioLines,
  Bell,
  Bot,
  Captions,
  ChartNoAxesColumn,
  Clapperboard,
  Cpu,
  FileType,
  Fingerprint,
  Globe,
  ScanText,
  Search,
  Settings,
  ShieldCheck,
  Sparkles,
  Terminal,
  Upload,
  type LucideIcon,
  KeyRound,
} from "lucide-react";
import Link from "next/link";
import { useCallback, useState } from "react";

import { Admin } from "@/app/openapi-client";
import { isUnreachable, ServerUnreachable } from "@/components/errors/error-states";
import { SECTIONS, type SectionId, type SettingsView } from "@/components/settings/model";
import { SettingsSection } from "@/components/settings/section";
import { EmptyState, Skeleton } from "@/components/ui/states";
import { Button } from "@/components/ui/button";
import { data, useApiClient } from "@/lib/api/browser";
import { useArchive } from "@/lib/hooks/session";
import { cn } from "@/lib/utils";

const ICON: Record<SectionId, LucideIcon> = {
  transcription: Captions,
  "speaker-separation": AudioLines,
  "voice-ids": Fingerprint,
  analysis: ScanText,
  llm: Sparkles,
  ai: Bot,
  search: Search,
  reports: ChartNoAxesColumn,
  video: Clapperboard,
  workers: Cpu,
  access: ShieldCheck,
  notifications: Bell,
  uploads: Upload,
  documents: FileType,
  tokens: KeyRound,
  iiif: Globe,
  startup: Terminal,
};

/** Settings (admins): the backend's sections in a sub-nav, one section at a time (ST1–ST3, MD5). */
export function SettingsShell({ section }: { section: SectionId }) {
  const client = useApiClient();
  const { admin, me } = useArchive();
  const settings = useQuery({
    queryKey: ["settings"],
    queryFn: async () => (await data(Admin.getSettings({ client }))) as unknown as SettingsView,
    enabled: admin,
  });
  const [withErrors, setWithErrors] = useState<Set<SectionId>>(new Set());
  const onErrors = useCallback((id: SectionId, has: boolean) => {
    setWithErrors((s) => {
      if (s.has(id) === has) return s;
      const n = new Set(s);
      if (has) n.add(id);
      else n.delete(id);
      return n;
    });
  }, []);

  if (me && !admin)
    return (
      <EmptyState
        icon={<Settings />}
        title="Settings are for admins"
        actions={
          <Button asChild>
            <Link href="/">Go to Home</Link>
          </Button>
        }
      >
        Platform admins change how the archive transcribes, analyses and publishes. Your own profile and API tokens are
        in the account menu.
      </EmptyState>
    );

  return (
    <div className="grid grid-cols-[minmax(0,1fr)] md:min-h-[calc(100vh-64px)] md:grid-cols-[230px_minmax(0,1fr)]">
      <nav
        aria-label="Settings sections"
        className="flex min-w-0 flex-col gap-px border-b border-border bg-surface px-2.5 py-[18px] md:sticky md:top-16 md:h-[calc(100vh-64px)] md:overflow-y-auto md:border-b-0 md:border-r"
      >
        <div className="px-2.5 pb-3.5 pt-1 text-[20px] font-bold leading-none text-fg">Settings</div>
        <div className="flex gap-1 overflow-x-auto md:flex-col md:gap-px md:overflow-visible">
          {SECTIONS.map((s) => {
            const Icon = ICON[s.id];
            const on = s.id === section;
            return (
              <Link
                key={s.id}
                href={`/settings/${s.id}`}
                aria-current={on ? "page" : undefined}
                className={cn(
                  "flex h-8 shrink-0 items-center gap-2.5 whitespace-nowrap rounded-sm px-2.5 text-[13.5px] leading-none transition-colors duration-fast",
                  on
                    ? "bg-blue-surface font-bold text-fg-accent"
                    : "font-medium text-fg-strong hover:bg-surface-neutral",
                )}
              >
                <Icon aria-hidden className="size-[15px] shrink-0" />
                <span className="flex-1">{s.label}</span>
                {withErrors.has(s.id) && (
                  <span className="size-[7px] rounded-full bg-red" role="img" aria-label="has errors" />
                )}
              </Link>
            );
          })}
        </div>
      </nav>
      <div className="min-w-0">
        {settings.isPending ? (
          <div className="flex max-w-[820px] flex-col gap-4 px-8 py-6" aria-busy="true" aria-label="Loading settings">
            <Skeleton className="h-7 w-48" />
            <Skeleton className="h-4 w-3/4" />
            <Skeleton className="h-10 w-full" />
            <Skeleton className="h-10 w-full" />
            <Skeleton className="h-24 w-full" />
          </div>
        ) : settings.isError ? (
          isUnreachable(settings.error) ? (
            <ServerUnreachable error={settings.error} onRetry={() => settings.refetch()} />
          ) : (
            <EmptyState
              tone="error"
              icon={<Settings />}
              title="Couldn’t load the settings"
              actions={<Button onClick={() => settings.refetch()}>Try again</Button>}
            >
              {settings.error.message}
            </EmptyState>
          )
        ) : (
          <SettingsSection key={section} id={section} view={settings.data} onErrors={onErrors} />
        )}
      </div>
    </div>
  );
}
