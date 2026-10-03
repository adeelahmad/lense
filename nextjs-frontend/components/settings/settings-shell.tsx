"use client";

import { useQuery } from "@tanstack/react-query";
import {
  Activity,
  Archive,
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
  Users,
  Mail,
  PackageCheck,
} from "lucide-react";
import Link from "next/link";
import { useCallback, useState } from "react";

import { Admin } from "@/app/openapi-client";
import { isUnreachable, ServerUnreachable } from "@/components/errors/error-states";
import {
  type AnySectionId,
  isWorkspaceSection,
  SECTIONS,
  type SectionId,
  type SettingsView,
  WORKSPACE_SECTIONS,
  type WorkspaceSectionId,
} from "@/components/settings/model";
import { SettingsSection } from "@/components/settings/section";
import { SpeakersPage } from "@/components/speakers/speakers-page";
import { EmptyState, Skeleton } from "@/components/ui/states";
import { Button } from "@/components/ui/button";
import { data, useApiClient } from "@/lib/api/browser";
import { useArchive } from "@/lib/hooks/session";
import { cn } from "@/lib/utils";

const ICON: Record<AnySectionId, LucideIcon> = {
  speakers: Users,
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
  components: PackageCheck,
  access: ShieldCheck,
  notifications: Bell,
  mail: Mail,
  telemetry: Activity,
  fedora: Archive,
  uploads: Upload,
  documents: FileType,
  tokens: KeyRound,
  iiif: Globe,
  startup: Terminal,
};

function SectionLink({ id, label, on, errors }: { id: AnySectionId; label: string; on: boolean; errors?: boolean }) {
  const Icon = ICON[id];
  return (
    <Link
      href={`/settings/${id}`}
      aria-current={on ? "page" : undefined}
      className={cn(
        "flex h-8 shrink-0 items-center gap-2.5 whitespace-nowrap rounded-sm px-2.5 text-[13.5px] leading-none transition-colors duration-fast",
        on ? "bg-blue-surface font-bold text-fg-accent" : "font-medium text-fg-strong hover:bg-surface-neutral",
      )}
    >
      <Icon aria-hidden className="size-[15px] shrink-0" />
      <span className="flex-1">{label}</span>
      {errors && <span className="size-[7px] rounded-full bg-red" role="img" aria-label="has errors" />}
    </Link>
  );
}

function GroupLabel({ children }: { children: React.ReactNode }) {
  return (
    <div className="hidden px-2.5 pb-1 pt-3 text-[11px] font-bold uppercase tracking-wide text-fg-muted md:block">
      {children}
    </div>
  );
}

/** Settings: Speakers for every member, then the archive's settings for admins (ST1–ST3, MD5), one at a time. */
export function SettingsShell({ section }: { section: AnySectionId }) {
  const { admin } = useArchive();
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
  return (
    <div className="grid grid-cols-[minmax(0,1fr)] md:min-h-[calc(100vh-64px)] md:grid-cols-[230px_minmax(0,1fr)]">
      <nav
        aria-label="Settings sections"
        className="flex min-w-0 flex-col gap-px border-b border-border bg-surface px-2.5 py-[18px] md:sticky md:top-16 md:h-[calc(100vh-64px)] md:overflow-y-auto md:border-b-0 md:border-r"
      >
        <div className="px-2.5 pb-1.5 pt-1 text-[20px] font-bold leading-none text-fg">Settings</div>
        <div className="flex gap-1 overflow-x-auto md:flex-col md:gap-px md:overflow-visible">
          <GroupLabel>Your namespaces</GroupLabel>
          {WORKSPACE_SECTIONS.map((s) => (
            <SectionLink key={s.id} id={s.id} label={s.label} on={s.id === section} />
          ))}
          {admin && (
            <>
              <GroupLabel>Archive</GroupLabel>
              {SECTIONS.map((s) => (
                <SectionLink key={s.id} id={s.id} label={s.label} on={s.id === section} errors={withErrors.has(s.id)} />
              ))}
            </>
          )}
        </div>
      </nav>
      <div className="min-w-0">
        {isWorkspaceSection(section) ? (
          <WorkspaceSection id={section} />
        ) : (
          <ArchiveSettings section={section} onErrors={onErrors} />
        )}
      </div>
    </div>
  );
}

function WorkspaceSection({ id }: { id: WorkspaceSectionId }) {
  switch (id) {
    case "speakers":
      return <SpeakersPage />;
  }
}

/** One of the archive's settings sections (admins). */
function ArchiveSettings({
  section,
  onErrors,
}: {
  section: SectionId;
  onErrors: (id: SectionId, has: boolean) => void;
}) {
  const client = useApiClient();
  const { admin, me } = useArchive();
  const settings = useQuery({
    queryKey: ["settings"],
    queryFn: async () => (await data(Admin.getSettings({ client }))) as unknown as SettingsView,
    enabled: admin,
  });
  if (me && !admin)
    return (
      <EmptyState
        icon={<Settings />}
        title="These settings are for admins"
        actions={
          <Button asChild>
            <Link href="/settings/speakers">Go to Speakers</Link>
          </Button>
        }
      >
        Platform admins change how the archive transcribes, analyses and publishes. Your own profile and API tokens are
        in the account menu.
      </EmptyState>
    );

  return settings.isPending ? (
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
  );
}
