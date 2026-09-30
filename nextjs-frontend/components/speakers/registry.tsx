"use client";

import { ChevronRight } from "lucide-react";
import { useRouter } from "next/navigation";
import { useMemo, useState } from "react";

import type { Speaker } from "@/app/openapi-client/types.gen";
import { speakerTone, talkTime } from "@/components/speakers/format";
import { SpeakerAvatar } from "@/components/speakers/parts";
import { SortTh, Table, Td, Th, THead, Tr } from "@/components/ui/table";
import { Tooltip } from "@/components/ui/tooltip";
import { count, shortDate } from "@/lib/format";
import { isUnnamed } from "@/components/speakers/format";

type SortKey = "name" | "talk" | "recordings" | "last";

/** SP1: a namespace's speakers — talk time, recordings, voiceprint, last heard. Rows open the profile. */
export function Registry({ speakers, heard }: { speakers: Speaker[]; heard: Map<number, string> }) {
  const router = useRouter();
  const [sort, setSort] = useState<{ key: SortKey; dir: "asc" | "desc" }>({ key: "talk", dir: "desc" });
  const max = Math.max(1, ...speakers.map((s) => s.talk_ms ?? 0));
  const rows = useMemo(() => {
    const v = (s: Speaker): string | number =>
      sort.key === "name" ? s.display.toLowerCase() : sort.key === "talk" ? (s.talk_ms ?? 0) : sort.key === "recordings" ? (s.recordings ?? 0) : (heard.get(s.id) ?? "");
    return [...speakers].sort((a, b) => {
      const x = v(a);
      const y = v(b);
      const c = x < y ? -1 : x > y ? 1 : 0;
      return sort.dir === "asc" ? c : -c;
    });
  }, [speakers, sort, heard]);
  const by = (key: SortKey) => ({
    active: sort.key === key,
    dir: sort.dir,
    onSort: () => setSort((s) => ({ key, dir: s.key === key && s.dir === "desc" ? "asc" : key === "name" ? "asc" : "desc" })),
  });
  return (
    <div className="overflow-hidden rounded-md border border-border">
      <Table aria-label="Speakers">
        <THead className="border-t-0">
          <tr>
            <SortTh {...by("name")}>Name</SortTh>
            <SortTh {...by("talk")} className="w-[190px]">
              Talk time
            </SortTh>
            <SortTh {...by("recordings")} className="w-[110px]">
              Recordings
            </SortTh>
            <Th className="hidden w-[200px] md:table-cell">Voiceprint</Th>
            <SortTh {...by("last")} className="hidden w-[140px] lg:table-cell">
              Last heard
            </SortTh>
            <Th className="w-8">
              <span className="sr-only">Open</span>
            </Th>
          </tr>
        </THead>
        <tbody>
          {rows.map((s) => (
            <Tr key={s.id} className="h-[50px] cursor-pointer" onClick={() => router.push(`/speakers/${s.id}`)}>
              <Td>
                <span className="flex items-center gap-2.5">
                  <SpeakerAvatar s={s} />
                  <a href={`/speakers/${s.id}`} onClick={(e) => e.stopPropagation()} className="font-semibold text-fg hover:text-fg-accent hover:underline">
                    {s.display}
                  </a>
                  {isUnnamed(s) && <span className="inline-flex h-5 items-center rounded-pill border border-gold-border bg-gold-surface px-[7px] text-[11px] font-semibold text-gold-dark">unnamed</span>}
                </span>
              </Td>
              <Td>
                <span className="tabular flex items-center gap-2">
                  <span className="h-[5px] w-[60px] overflow-hidden rounded-pill bg-surface-neutral" aria-hidden>
                    <span className="block h-full" style={{ width: `${Math.max(2, ((s.talk_ms ?? 0) / max) * 100)}%`, background: speakerTone(s.id) }} />
                  </span>
                  {talkTime(s.talk_ms)}
                </span>
              </Td>
              <Td className="tabular">{count(s.recordings)}</Td>
              <Td className="hidden md:table-cell">
                {s.has_voice ? (
                  <span className="flex items-center gap-1.5 text-green-dark">
                    <span aria-hidden className="font-extrabold">
                      ✓
                    </span>
                    Enrolled
                  </span>
                ) : (
                  <Tooltip content="Voiceprints are built from clean audio (Settings → Voice IDs). Speakers without one are matched by name only.">
                    <span tabIndex={0} className="text-fg-muted">
                      No voiceprint
                    </span>
                  </Tooltip>
                )}
              </Td>
              <Td className="tabular hidden text-fg-secondary lg:table-cell">{heard.get(s.id) ? shortDate(heard.get(s.id)) : "—"}</Td>
              <Td>
                <ChevronRight aria-hidden className="size-4 text-fg-muted" />
              </Td>
            </Tr>
          ))}
        </tbody>
      </Table>
    </div>
  );
}
