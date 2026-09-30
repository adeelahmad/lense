"use client";

import { useQuery } from "@tanstack/react-query";
import { ArrowUp, AudioLines, CalendarRange, FolderClosed, FolderSearch, Plus, Sparkles, Wrench, X } from "lucide-react";
import { useEffect, useRef, useState, type ReactNode } from "react";

import { Collections } from "@/app/openapi-client";
import { datesLabel, namespaceSize, recordingsLabel, type Scope } from "@/components/chat/scope";
import { useRecordingIndex, useSpeakerDirectory } from "@/components/search/data";
import { talkTime } from "@/components/speakers/format";
import { Button } from "@/components/ui/button";
import { Checkbox, Input, SearchInput } from "@/components/ui/field";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/menu";
import { Segmented } from "@/components/ui/tabs";
import { data, useApiClient } from "@/lib/api/browser";
import { count } from "@/lib/format";
import { useArchive } from "@/lib/hooks/session";
import { cn } from "@/lib/utils";

function ScopeChip({ icon, label, size, onRemove, strong }: { icon: ReactNode; label: string; size?: string; onRemove?: () => void; strong?: boolean }) {
  return (
    <span
      className={cn(
        "inline-flex h-7 max-w-full items-center gap-1.5 rounded-pill border pl-2.5 text-[12px] font-semibold [&_svg]:size-[13px] [&_svg]:shrink-0",
        onRemove ? "pr-1" : "pr-2.5",
        strong ? "border-blue-border bg-blue-surface text-fg-accent" : "border-border bg-background text-fg-strong",
      )}
    >
      {icon}
      <span className="truncate">{label}</span>
      {size && <span className="hidden whitespace-nowrap font-medium text-fg-muted sm:inline">{size}</span>}
      {onRemove && (
        <button type="button" onClick={onRemove} aria-label={`Remove ${label} from scope`} className="grid size-5 place-items-center rounded-full hover:bg-black/5">
          <X className="!size-3" />
        </button>
      )}
    </span>
  );
}

type Section = "namespaces" | "recordings" | "speakers" | "dates" | "collection";

/** What a conversation may draw on: namespaces, recordings, speakers, dates, or a saved collection's recordings. */
function ScopePicker({ scope, onChange, onClose }: { scope: Scope; onChange: (s: Scope) => void; onClose: () => void }) {
  const client = useApiClient();
  const { namespaces } = useArchive();
  const [section, setSection] = useState<Section>("namespaces");
  const [filter, setFilter] = useState("");
  const index = useRecordingIndex(section === "recordings" || section === "collection");
  const dir = useSpeakerDirectory(section === "speakers");
  const cols = useQuery({ queryKey: ["collections"], queryFn: () => data(Collections.listCollections({ client })), enabled: section === "collection", staleTime: 60_000 });
  const inNs = (ns: string | null | undefined) => !scope.namespaces?.length || (ns != null && scope.namespaces.includes(ns));
  const toggle = <T,>(list: T[] | undefined, v: T): T[] => (list?.includes(v) ? list.filter((x) => x !== v) : [...(list ?? []), v]);
  const f = filter.trim().toLowerCase();
  return (
    <div className="flex w-[380px] max-w-[calc(100vw-32px)] flex-col gap-3 p-3">
      <Segmented
        value={section}
        onChange={(v) => {
          setSection(v as Section);
          setFilter("");
        }}
        className="self-start"
        items={[
          { value: "namespaces", label: "Namespaces" },
          { value: "recordings", label: "Recordings" },
          { value: "speakers", label: "Speakers" },
          { value: "dates", label: "Dates" },
          { value: "collection", label: "Collection" },
        ]}
      />
      {(section === "recordings" || section === "speakers") && <SearchInput value={filter} onChange={(e) => setFilter(e.target.value)} placeholder={`Filter ${section}`} aria-label={`Filter ${section}`} />}
      <div className="max-h-[300px] overflow-y-auto">
        {section === "namespaces" && (
          <ul className="m-0 flex list-none flex-col gap-2 p-0">
            {namespaces.map((n) => (
              <li key={n.name} className="flex items-center gap-2">
                <Checkbox checked={Boolean(scope.namespaces?.includes(n.name))} onCheckedChange={() => onChange({ ...scope, namespaces: toggle(scope.namespaces, n.name) })} label={n.name} />
                <span className="ml-auto text-[12px] text-fg-muted">{namespaceSize([n])}</span>
              </li>
            ))}
            <li className="pt-1 text-[12px] text-fg-muted">None ticked means all your namespaces. Scope never goes beyond what you can read.</li>
          </ul>
        )}
        {section === "recordings" && (
          <ul className="m-0 flex list-none flex-col gap-2 p-0">
            {index.isLoading && <li className="text-[13px] text-fg-muted">Loading recordings…</li>}
            {(index.data ?? [])
              .filter((r) => inNs(r.namespace) && (!f || (r.title ?? "").toLowerCase().includes(f)))
              .slice(0, 60)
              .map((r) => (
                <li key={r.id} className="flex items-center gap-2">
                  <Checkbox checked={Boolean(scope.recordings?.includes(r.id))} onCheckedChange={() => onChange({ ...scope, recordings: toggle(scope.recordings, r.id) })} label={r.title ?? `Recording ${r.id}`} />
                  <span className="ml-auto shrink-0 text-[12px] text-fg-muted">{r.namespace}</span>
                </li>
              ))}
          </ul>
        )}
        {section === "speakers" && (
          <ul className="m-0 flex list-none flex-col gap-2 p-0">
            {dir.isLoading && <li className="text-[13px] text-fg-muted">Loading speakers…</li>}
            {dir.speakers
              .filter((s) => inNs(s.namespace) && (!f || s.display.toLowerCase().includes(f)))
              .sort((a, b) => (b.talk_ms ?? 0) - (a.talk_ms ?? 0))
              .slice(0, 60)
              .map((s) => (
                <li key={s.id} className="flex items-center gap-2">
                  <Checkbox checked={Boolean(scope.speakers?.includes(s.id))} onCheckedChange={() => onChange({ ...scope, speakers: toggle(scope.speakers, s.id) })} label={s.display} />
                  <span className="ml-auto shrink-0 text-[12px] text-fg-muted">
                    {s.namespace} · {talkTime(s.talk_ms)}
                  </span>
                </li>
              ))}
          </ul>
        )}
        {section === "dates" && (
          <div className="grid grid-cols-2 gap-2">
            <label className="flex flex-col gap-1 text-[12.5px] font-bold text-fg-strong">
              From
              <Input type="date" value={scope.from ?? ""} onChange={(e) => onChange({ ...scope, from: e.target.value || undefined })} />
            </label>
            <label className="flex flex-col gap-1 text-[12.5px] font-bold text-fg-strong">
              To
              <Input type="date" value={scope.to ?? ""} onChange={(e) => onChange({ ...scope, to: e.target.value || undefined })} />
            </label>
          </div>
        )}
        {section === "collection" && (
          <ul className="m-0 flex list-none flex-col gap-1 p-0">
            {cols.isLoading && <li className="text-[13px] text-fg-muted">Loading collections…</li>}
            {cols.data?.length === 0 && <li className="text-[13px] text-fg-muted">No saved collections yet.</li>}
            {(cols.data ?? []).map((c) => (
              <li key={c.id}>
                <CollectionOption id={c.id} name={c.name} n={c.count} onPick={(ids) => (onChange({ ...scope, recordings: ids }), onClose())} />
              </li>
            ))}
            <li className="pt-1 text-[12px] text-fg-muted">A collection limits the conversation to its recordings as they are now.</li>
          </ul>
        )}
      </div>
      <div className="flex justify-end">
        <Button size="sm" variant="primary" onClick={onClose}>
          Done
        </Button>
      </div>
    </div>
  );
}

function CollectionOption({ id, name, n, onPick }: { id: number; name: string; n: number; onPick: (ids: number[]) => void }) {
  const client = useApiClient();
  const [busy, setBusy] = useState(false);
  return (
    <button
      type="button"
      disabled={busy || n === 0}
      onClick={async () => {
        setBusy(true);
        try {
          const c = await data(Collections.getCollection({ client, path: { cid: id } }));
          onPick(c.recordings.map((r) => r.id));
        } finally {
          setBusy(false);
        }
      }}
      className="flex w-full items-center gap-2 rounded-sm px-2 py-1.5 text-left text-[13.5px] text-fg hover:bg-surface-neutral disabled:opacity-50"
    >
      <FolderSearch className="size-4 text-fg-secondary" aria-hidden />
      <span className="flex-1 truncate">{name}</span>
      <span className="tabular text-[12px] text-fg-muted">{busy ? "…" : count(n)}</span>
    </button>
  );
}

/** Scope chips (each shows its size), "+ Scope", and which model answers. */
export function ScopeBar({ scope, onChange, model, tools, className }: { scope: Scope; onChange: (s: Scope) => void; model: string | null; tools: boolean | null; className?: string }) {
  const { namespaces } = useArchive();
  const [open, setOpen] = useState(false);
  const index = useRecordingIndex(Boolean(scope.recordings?.length));
  const dir = useSpeakerDirectory(Boolean(scope.speakers?.length));
  const chosen = namespaces.filter((n) => scope.namespaces?.includes(n.name));
  const recs = scope.recordings?.length ? recordingsLabel(scope.recordings, index.byId) : null;
  const spk = scope.speakers?.map((id) => dir.speakers.find((s) => s.id === id)?.display ?? `#${id}`);
  return (
    <div className={cn("flex flex-wrap items-center gap-1.5", className)}>
      <span className="sr-only">Scope:</span>
      {chosen.length === 0 && <ScopeChip strong icon={<FolderClosed />} label="All my namespaces" size={namespaceSize(namespaces)} />}
      {chosen.map((n, i) => (
        <ScopeChip key={n.name} strong={i === 0} icon={<FolderClosed />} label={n.name} size={namespaceSize([n])} onRemove={() => onChange({ ...scope, namespaces: scope.namespaces?.filter((x) => x !== n.name) })} />
      ))}
      {recs && <ScopeChip icon={<FolderSearch />} label={recs.label} size={recs.size} onRemove={() => onChange({ ...scope, recordings: undefined })} />}
      {spk && <ScopeChip icon={<AudioLines />} label={spk.join(", ")} onRemove={() => onChange({ ...scope, speakers: undefined })} />}
      {(scope.from || scope.to) && <ScopeChip icon={<CalendarRange />} label={datesLabel(scope.from, scope.to)} onRemove={() => onChange({ ...scope, from: undefined, to: undefined })} />}
      <Popover open={open} onOpenChange={setOpen}>
        <PopoverTrigger asChild>
          <button type="button" className="inline-flex h-7 items-center gap-1 rounded-pill border border-dashed border-border px-2.5 text-[12px] font-semibold text-fg-secondary hover:border-fg-muted hover:text-fg">
            <Plus className="size-3" aria-hidden /> Scope
          </button>
        </PopoverTrigger>
        <PopoverContent align="start">
          <ScopePicker scope={scope} onChange={onChange} onClose={() => setOpen(false)} />
        </PopoverContent>
      </Popover>
      <span className="flex-1" />
      {model && (
        <span className="hidden items-center gap-1.5 text-[12px] font-semibold text-fg-secondary sm:flex" title="The model is set in Settings → LLM provider">
          {tools ? <Wrench className="size-[13px]" aria-hidden /> : <Sparkles className="size-[13px]" aria-hidden />}
          {tools ? `tools on · ${model}` : model}
        </span>
      )}
    </div>
  );
}

/** The question box: Enter sends, Shift+Enter makes a new line. */
export function Composer({
  value,
  onChange,
  onSend,
  busy,
  placeholder,
  autoFocus,
  className,
}: {
  value: string;
  onChange: (v: string) => void;
  onSend: () => void;
  busy: boolean;
  placeholder: string;
  autoFocus?: boolean;
  className?: string;
}) {
  const ref = useRef<HTMLTextAreaElement>(null);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 180)}px`;
  }, [value]);
  const ready = value.trim().length > 0 && !busy;
  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        if (ready) onSend();
      }}
      className={cn(
        "flex items-end gap-2 rounded-[14px] border border-border bg-background py-2.5 pl-3.5 pr-2.5 transition-[border-color,box-shadow] duration-fast focus-within:border-blue focus-within:shadow-[0_0_0_3px_var(--intent-surface)]",
        className,
      )}
    >
      <textarea
        ref={ref}
        rows={1}
        value={value}
        autoFocus={autoFocus}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
            e.preventDefault();
            if (ready) onSend();
          }
        }}
        placeholder={placeholder}
        aria-label="Your question"
        className="max-h-[180px] min-h-[24px] flex-1 resize-none bg-transparent py-1.5 text-[15px] leading-normal text-fg outline-none placeholder:text-fg-muted"
      />
      <button
        type="submit"
        aria-label={busy ? "Answering…" : "Send"}
        disabled={!ready}
        className={cn("grid size-9 shrink-0 place-items-center rounded-full transition-colors duration-fast", ready ? "bg-blue text-white hover:bg-blue-dark" : "bg-surface-neutral text-fg-muted")}
      >
        <ArrowUp className="size-[18px]" />
      </button>
    </form>
  );
}
