"use client";

import { AudioLines } from "lucide-react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useMemo } from "react";

import { useRecordingIndex } from "@/components/search/data";
import { MergeHistory, OtherNamespaces, useVoiceSuggestions } from "@/components/speakers/history";
import { useNamespaceSpeakers } from "@/components/speakers/parts";
import { lastHeard, reviewPairs } from "@/components/speakers/derive";
import { Registry } from "@/components/speakers/registry";
import { ReviewQueue } from "@/components/speakers/review";
import { Banner } from "@/components/ui/banner";
import { Button } from "@/components/ui/button";
import { Select } from "@/components/ui/field";
import { EmptyState, PageHeader, SkeletonRows } from "@/components/ui/states";
import { Tabs } from "@/components/ui/tabs";
import { plural } from "@/lib/format";
import { useArchive } from "@/lib/hooks/session";

const TABS = ["registry", "review", "history", "links"] as const;
type Tab = (typeof TABS)[number];

/** SP1–SP3: the speakers of one namespace — registry, review queue, merge history, links to other namespaces. */
export function SpeakersPage() {
  const params = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();
  const { namespaces, namespace: topNs } = useArchive();
  const ns = params.get("ns") && namespaces.some((n) => n.name === params.get("ns")) ? params.get("ns")! : (topNs ?? namespaces[0]?.name ?? null);
  const tab: Tab = (TABS as readonly string[]).includes(params.get("tab") ?? "") ? (params.get("tab") as Tab) : "registry";
  const dir = useNamespaceSpeakers(ns);
  const index = useRecordingIndex();
  const voice = useVoiceSuggestions(ns ?? "");

  const set = (k: string, v: string | null) => {
    const p = new URLSearchParams(params.toString());
    if (v) p.set(k, v);
    else p.delete(k);
    router.replace(`${pathname}?${p}`);
  };

  const speakers = useMemo(() => dir.data?.speakers ?? [], [dir.data]);
  const pairs = useMemo(() => reviewPairs(speakers), [speakers]);
  const heard = useMemo(() => (ns ? lastHeard(speakers, index.data ?? [], ns) : new Map<number, string>()), [speakers, index.data, ns]);
  const links = dir.data?.links ?? [];
  const linkCount = links.length + voice.suggestions.filter((s) => !links.some((l) => [l.a, l.b].includes(s.a.id) && [l.a, l.b].includes(s.b.id))).length;

  if (!namespaces.length)
    return (
      <div className="px-4 py-6 md:px-6">
        <PageHeader title="Speakers" />
        <EmptyState icon={<AudioLines />} title="No namespaces yet">
          Speakers are kept per namespace. Once you have access to one, its voices show here.
        </EmptyState>
      </div>
    );

  return (
    <div className="flex flex-col gap-3 px-4 py-6 md:px-6">
      <PageHeader
        className="mb-0"
        title="Speakers"
        meta={ns && dir.data ? `${ns} · ${plural(speakers.length, "speaker")}` : undefined}
        actions={
          namespaces.length > 1 && (
            <label className="flex items-center gap-2 text-[13px] font-semibold text-fg-secondary">
              Namespace
              <Select size="sm" className="w-[200px]" value={ns ?? ""} onChange={(e) => set("ns", e.target.value)} options={namespaces.map((n) => n.name)} aria-label="Namespace" />
            </label>
          )
        }
      />
      <Tabs
        aria-label="Speakers views"
        value={tab}
        onChange={(v) => set("tab", v === "registry" ? null : v)}
        items={[
          { value: "registry", label: "Registry", count: dir.data ? speakers.length : undefined },
          { value: "review", label: "Review queue", count: dir.data ? pairs.length : undefined },
          { value: "history", label: "Merge history" },
          { value: "links", label: "Other namespaces", count: linkCount || undefined },
        ]}
      />
      <div className="pt-1">
        {dir.isLoading && <SkeletonRows rows={5} />}
        {dir.isError && (
          <Banner
            tone="error"
            title="Couldn’t load the speakers."
            action={
              <Button size="sm" variant="secondary" onClick={() => dir.refetch()}>
                Try again
              </Button>
            }
          >
            {dir.error.message}
          </Banner>
        )}
        {dir.data && ns && tab === "registry" && (
          <div className="flex flex-col gap-3">
            {speakers.length ? (
              <Registry speakers={speakers} heard={heard} />
            ) : (
              <EmptyState icon={<AudioLines />} title={`No speakers in ${ns} yet`} className="rounded-lg border border-border">
                Speakers appear once recordings are diarized, or when an imported transcript names them.
              </EmptyState>
            )}
            <p className="m-0 text-[12.5px] leading-snug text-fg-secondary">Voiceprints are built from clean audio (Settings → Voice IDs). Speakers without one are still matched, less reliably.</p>
          </div>
        )}
        {dir.data && ns && tab === "review" && <ReviewQueue pairs={pairs} ns={ns} />}
        {dir.data && ns && tab === "history" && <MergeHistory merges={dir.data.merges} ns={ns} />}
        {dir.data && ns && tab === "links" && <OtherNamespaces links={links} ns={ns} />}
      </div>
    </div>
  );
}
