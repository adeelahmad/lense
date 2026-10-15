"use client";

import { useQuery } from "@tanstack/react-query";
import {
  LockKeyhole,
  Cloud,
  Activity,
  BarChart3,
  Archive,
  AudioLines,
  AudioWaveform,
  Bell,
  Bot,
  Captions,
  ChartNoAxesColumn,
  ChevronDown,
  Clapperboard,
  Cpu,
  Database,
  DatabaseBackup,
  EyeOff,
  HardDrive,
  FileLock,
  FileType,
  FolderTree,
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
  MessagesSquare,
  Send,
  AtSign,
  Hash,
  PackageCheck,
  RadioTower,
  Lock,
  RefreshCw,
  Network,
  ScrollText,
} from "lucide-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { Admin } from "@/app/openapi-client";
import { isUnreachable, ServerUnreachable } from "@/components/errors/error-states";
import { findSettings, hitHref, settingHref, SETTINGS_GROUPS, type SettingHit } from "@/components/settings/find";
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
import { SearchInput } from "@/components/ui/field";
import { data, useApiClient } from "@/lib/api/browser";
import { useArchive } from "@/lib/hooks/session";
import { cn } from "@/lib/utils";

const ICON: Record<AnySectionId, LucideIcon> = {
  speakers: Users,
  transcription: Captions,
  "speaker-separation": AudioLines,
  "speech-providers": AudioWaveform,
  "voice-ids": Fingerprint,
  analysis: ScanText,
  llm: Sparkles,
  "local-model": HardDrive,
  ai: Bot,
  search: Search,
  reports: ChartNoAxesColumn,
  video: Clapperboard,
  nsfw: EyeOff,
  workers: Cpu,
  components: PackageCheck,
  access: ShieldCheck,
  "remote-access": Cloud,
  https: Lock,
  "sign-in": LockKeyhole,
  encryption: FileLock,
  notifications: Bell,
  mail: Mail,
  bridge: MessagesSquare,
  telegram: Send,
  slack: Hash,
  mailbox: AtSign,
  telemetry: Activity,
  analytics: BarChart3,
  updates: RefreshCw,
  logs: ScrollText,
  fedora: Archive,
  sensors: RadioTower,
  nfs: FolderTree,
  smb: Network,
  shares: FolderTree,
  uploads: Upload,
  storage: Database,
  backups: DatabaseBackup,
  documents: FileType,
  tokens: KeyRound,
  iiif: Globe,
  startup: Terminal,
};

function SectionLink({
  id,
  label,
  on,
  errors,
  onNavigate,
}: {
  id: AnySectionId;
  label: string;
  on: boolean;
  errors?: boolean;
  onNavigate?: () => void;
}) {
  const Icon = ICON[id];
  return (
    <Link
      href={`/settings/${id}`}
      onClick={onNavigate}
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
  return <div className="px-2.5 pb-1 pt-3 text-[11px] font-bold uppercase tracking-wide text-fg-muted">{children}</div>;
}

/** What Find found: each section, with the fields in it that matched, under its group. */
function Found({ hits, section, onNavigate }: { hits: SettingHit[]; section: AnySectionId; onNavigate: () => void }) {
  if (!hits.length) return <p className="px-2.5 pt-3 text-[13px] text-fg-secondary">No setting matches that.</p>;
  let group = "";
  return hits.map((h) => {
    const label = h.group !== group ? h.group : null;
    group = h.group;
    return (
      <div key={h.section} className="flex flex-col gap-px">
        {label && <GroupLabel>{label}</GroupLabel>}
        <SectionLink id={h.section} label={h.label} on={h.section === section} onNavigate={onNavigate} />
        {h.fields.slice(0, 4).map((f) => (
          <Link
            key={f.key}
            href={settingHref(h.section, f)}
            onClick={onNavigate}
            className="truncate rounded-sm py-1 pl-[34px] pr-2.5 text-[12.5px] text-fg-secondary hover:bg-surface-neutral hover:text-fg"
          >
            {f.label}
          </Link>
        ))}
      </div>
    );
  });
}

/** Settings: Speakers for every member, then the archive's settings for admins (ST1–ST3, MD5), one at a time. On a
 * phone the sections are a list that opens from the section's name, rather than a strip that scrolls sideways. */
export function SettingsShell({ section }: { section: AnySectionId }) {
  const { admin } = useArchive();
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [term, setTerm] = useState("");
  const hits = useMemo(() => findSettings(term, admin), [term, admin]);
  const close = () => {
    setOpen(false);
    setTerm("");
  };
  const find = useRef<HTMLInputElement>(null);
  // "/" finds a setting, as it finds a word in a transcript
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement;
      if (e.key !== "/" || e.metaKey || e.ctrlKey || e.altKey) return;
      if (["INPUT", "TEXTAREA", "SELECT"].includes(t.tagName) || t.isContentEditable || !find.current) return;
      e.preventDefault();
      setOpen(true);
      setTimeout(() => find.current?.focus(), 0);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
  const current = [...WORKSPACE_SECTIONS, ...SECTIONS].find((s) => s.id === section);
  const CurrentIcon = ICON[section];
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
        <div className="flex items-center gap-2">
          <div className="flex-1 px-2.5 pb-1.5 pt-1 text-[20px] font-bold leading-none text-fg">Settings</div>
          <button
            type="button"
            aria-expanded={open}
            aria-controls="settings-sections"
            onClick={() => setOpen((o) => !o)}
            className="flex h-9 min-w-0 items-center gap-2 rounded-sm border border-border bg-background px-2.5 text-[13.5px] font-semibold text-fg md:hidden"
          >
            <CurrentIcon aria-hidden className="size-[15px] shrink-0 text-fg-secondary" />
            <span className="sr-only">Settings section: </span>
            <span className="truncate">{current?.label ?? "Sections"}</span>
            <ChevronDown aria-hidden className={cn("size-4 shrink-0 text-fg-muted", open && "rotate-180")} />
          </button>
        </div>
        <div id="settings-sections" className={cn("flex-col gap-px pt-1 md:flex md:pt-0", open ? "flex" : "hidden")}>
          {admin && (
            <SearchInput
              ref={find}
              aria-label="Find a setting"
              placeholder="Find a setting"
              value={term}
              onChange={(e) => setTerm(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Escape") setTerm("");
                if (e.key !== "Enter" || !hits[0]) return;
                e.preventDefault();
                router.push(hitHref(hits[0]));
                close();
              }}
              className="mx-0.5 mb-1 mt-2"
            />
          )}
          {term.trim() ? (
            <Found hits={hits} section={section} onNavigate={close} />
          ) : (
            <>
              <GroupLabel>Your namespaces</GroupLabel>
              {WORKSPACE_SECTIONS.map((s) => (
                <SectionLink key={s.id} id={s.id} label={s.label} on={s.id === section} onNavigate={close} />
              ))}
              {admin &&
                SETTINGS_GROUPS.map((g) => (
                  <div key={g.label} className="flex flex-col gap-px">
                    <GroupLabel>{g.label}</GroupLabel>
                    {g.sections.map((id) => (
                      <SectionLink
                        key={id}
                        id={id}
                        label={SECTIONS.find((s) => s.id === id)!.label}
                        on={id === section}
                        errors={withErrors.has(id)}
                        onNavigate={close}
                      />
                    ))}
                  </div>
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
  const field = useSearchParams().get("field");
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
            <Link href="/settings/speakers">Go to People</Link>
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
    <SettingsSection key={section} id={section} view={settings.data} onErrors={onErrors} field={field} />
  );
}
