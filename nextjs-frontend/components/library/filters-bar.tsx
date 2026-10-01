"use client";

import { Check, ChevronDown, Search, X } from "lucide-react";
import { forwardRef, useState, type ReactNode } from "react";

import {
  DATE_LABEL,
  DURATION_LABEL,
  MEDIA_LABEL,
  STATUS_FILTER_LABEL,
  languageName,
  type DateRange,
  type DurationRange,
  type Filters,
  type MediaFilter,
  type SpeakerChoice,
  type StatusFilter,
} from "@/components/library/model";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/menu";
import { count } from "@/lib/format";
import { useArchive } from "@/lib/hooks/session";
import { cn } from "@/lib/utils";

const chipBase =
  "inline-flex h-[30px] shrink-0 items-center gap-[5px] whitespace-nowrap rounded-pill border text-[12.5px] leading-none transition-colors duration-fast max-md:h-[34px] max-md:text-[13px]";
const chipOff = "border-border bg-background font-medium text-fg-strong hover:bg-surface";
const chipOn = "border-blue-border bg-blue-surface font-semibold text-fg-accent";

/** A filter chip: blue while active (with × to clear it), otherwise a label with a chevron that opens its options. */
function Chip({
  label,
  active,
  onClear,
  children,
  width = 240,
}: {
  label: ReactNode;
  active: boolean;
  onClear?: () => void;
  children: (close: () => void) => ReactNode;
  width?: number;
}) {
  const [open, setOpen] = useState(false);
  return (
    <span className={cn(chipBase, active ? chipOn : chipOff, "pl-0 pr-0")}>
      <Popover open={open} onOpenChange={setOpen}>
        <PopoverTrigger
          className={cn(
            "inline-flex h-full items-center gap-[5px] rounded-pill pl-[11px]",
            active && onClear ? "pr-1" : "pr-2",
          )}
        >
          {label}
          {!(active && onClear) && <ChevronDown className="size-[13px]" aria-hidden />}
        </PopoverTrigger>
        <PopoverContent className="p-1.5" align="start">
          <div style={{ width }}>{children(() => setOpen(false))}</div>
        </PopoverContent>
      </Popover>
      {active && onClear && (
        <button
          type="button"
          onClick={onClear}
          aria-label={`Clear ${typeof label === "string" ? label : "filter"}`}
          className="grid h-full place-items-center rounded-pill pl-0.5 pr-2 hover:text-fg"
        >
          <X className="size-[13px]" />
        </button>
      )}
    </span>
  );
}

/** A chip for a filter the backend can't answer yet: visible, disabled, and says why. */
function Option({
  on,
  onClick,
  children,
  multi,
}: {
  on: boolean;
  onClick: () => void;
  children: ReactNode;
  multi?: boolean;
}) {
  return (
    <button
      type="button"
      role={multi ? "menuitemcheckbox" : "menuitemradio"}
      aria-checked={on}
      onClick={onClick}
      className={cn(
        "flex h-9 w-full items-center gap-2.5 rounded-sm px-2.5 text-left text-[13.5px] text-fg hover:bg-surface-neutral",
        on && !multi && "font-semibold",
      )}
    >
      {multi ? (
        <span
          aria-hidden
          className={cn(
            "grid size-[18px] shrink-0 place-items-center rounded-xs border-2 text-white",
            on ? "border-blue bg-blue" : "border-fg-secondary bg-background",
          )}
        >
          {on && <Check className="size-3.5" strokeWidth={3} />}
        </span>
      ) : (
        <Check className={cn("size-4 text-blue", !on && "invisible")} aria-hidden />
      )}
      <span className="min-w-0 flex-1 truncate">{children}</span>
    </button>
  );
}

function Radio<T extends string>({
  value,
  options,
  onChange,
  close,
}: {
  value: T;
  options: Record<T, string>;
  onChange: (v: T) => void;
  close: () => void;
}) {
  return (
    <div role="menu">
      {(Object.keys(options) as T[]).map((k) => (
        <Option
          key={k}
          on={k === value}
          onClick={() => {
            onChange(k);
            close();
          }}
        >
          {options[k]}
        </Option>
      ))}
    </div>
  );
}

/** The filter box. `/` focuses it (see the library's keyboard handler). */
export const FilterInput = forwardRef<
  HTMLInputElement,
  { value: string; onChange: (v: string) => void; className?: string }
>(function FilterInput({ value, onChange, className }, ref) {
  return (
    <span className={cn("relative block", className)}>
      <Search
        aria-hidden
        className="pointer-events-none absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-fg-muted"
      />
      <input
        ref={ref}
        type="search"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder="Filter by title or speaker"
        aria-label="Filter recordings by title, namespace or speaker"
        aria-keyshortcuts="/"
        className="h-8 w-full rounded-sm border border-border bg-background pl-8 pr-2.5 text-[13px] text-fg outline-none placeholder:text-fg-muted focus:border-blue focus:shadow-[0_0_0_3px_var(--intent-surface)] max-md:h-11 max-md:rounded-[10px] max-md:border-transparent max-md:bg-surface-neutral max-md:pl-10 max-md:text-[15px]"
      />
    </span>
  );
});

/** Library filters: namespace, status, speaker, date, duration, media and tags, plus the ones the backend can't do yet. */
export function FiltersBar({
  filters,
  onChange,
  speakers,
  speakersLoading,
  tags,
  tagsLoading,
  inputRef,
  compact,
  trailing,
  origins = [],
  languages = [],
}: {
  filters: Filters;
  onChange: (f: Filters) => void;
  /** Everyone who speaks in the namespaces in scope, by name, most recordings first. */
  speakers: SpeakerChoice[];
  speakersLoading?: boolean;
  /** The tags in use in scope, most used first. */
  tags: { tag: string; recordings: number }[];
  tagsLoading?: boolean;
  inputRef: React.Ref<HTMLInputElement>;
  compact?: boolean;
  /** At the end of the bar: saved views. */
  trailing?: ReactNode;
  /** Where the recordings in scope came from (GET /recordings/origins), most first. */
  origins?: { origin: string; name: string; recordings: number }[];
  /** Their languages (GET /recordings/languages), most first; null is "not known". */
  languages?: { language?: string | null; recordings: number }[];
}) {
  const { namespaces, namespace, setNamespace } = useArchive();
  const [spkQuery, setSpkQuery] = useState("");
  const [tagQuery, setTagQuery] = useState("");
  const tagOn = (t: string) => filters.tags.some((x) => x.toLowerCase() === t.toLowerCase());
  const set = (patch: Partial<Filters>) => onChange({ ...filters, ...patch });

  const statusLabel =
    filters.statuses.length === 1 ? STATUS_FILTER_LABEL[filters.statuses[0]] : `${filters.statuses.length} statuses`;

  const chips = (
    <>
      <Chip label={`Namespace: ${namespace ?? "all"}`} active={Boolean(namespace)} onClear={() => setNamespace(null)}>
        {(close) => (
          <div role="menu">
            {[null, ...namespaces.map((n) => n.name)].map((name) => (
              <Option
                key={name ?? "*"}
                on={name === namespace}
                onClick={() => {
                  setNamespace(name);
                  close();
                }}
              >
                <span className="flex items-center justify-between gap-2">
                  {name ?? "All namespaces"}
                  <span className="tabular text-[12px] font-normal text-fg-muted">
                    {count(
                      name
                        ? ((namespaces.find((n) => n.name === name)?.recordings as number) ?? 0)
                        : namespaces.reduce((a, n) => a + ((n.recordings as number) ?? 0), 0),
                    )}
                  </span>
                </span>
              </Option>
            ))}
          </div>
        )}
      </Chip>
      <Chip
        label={
          filters.origins.length === 1
            ? `Source: ${origins.find((o) => o.origin === filters.origins[0])?.name ?? "1 source"}`
            : filters.origins.length
              ? `Source: ${filters.origins.length} sources`
              : "Source"
        }
        active={filters.origins.length > 0}
        onClear={() => set({ origins: [] })}
        width={240}
      >
        {() => (
          <div role="menu" aria-label="Source" className="max-h-64 overflow-y-auto">
            {origins.map((o) => {
              const on = filters.origins.includes(o.origin);
              return (
                <Option
                  key={o.origin}
                  multi
                  on={on}
                  onClick={() =>
                    set({
                      origins: on ? filters.origins.filter((x) => x !== o.origin) : [...filters.origins, o.origin],
                    })
                  }
                >
                  <span className="flex min-w-0 flex-1 items-center justify-between gap-2">
                    <span className="truncate">{o.name}</span>
                    <span className="tabular text-[12px] font-normal text-fg-muted">{count(o.recordings)}</span>
                  </span>
                </Option>
              );
            })}
            {!origins.length && <p className="px-2.5 py-3 text-[13px] text-fg-muted">No recordings yet.</p>}
          </div>
        )}
      </Chip>
      <Chip
        label={filters.statuses.length ? `Status: ${statusLabel}` : "Status"}
        active={filters.statuses.length > 0}
        onClear={() => set({ statuses: [] })}
        width={220}
      >
        {() => (
          <div role="menu" aria-label="Status">
            {(Object.keys(STATUS_FILTER_LABEL) as StatusFilter[]).map((s) => {
              const on = filters.statuses.includes(s);
              return (
                <Option
                  key={s}
                  multi
                  on={on}
                  onClick={() =>
                    set({
                      statuses: on ? filters.statuses.filter((x) => x !== s) : [...filters.statuses, s],
                    })
                  }
                >
                  {STATUS_FILTER_LABEL[s]}
                </Option>
              );
            })}
          </div>
        )}
      </Chip>
      <Chip
        label={filters.speaker ? `Speaker: ${filters.speaker.name}` : "Speaker"}
        active={Boolean(filters.speaker)}
        onClear={() => set({ speaker: null })}
        width={260}
      >
        {(close) => (
          <div>
            <input
              value={spkQuery}
              onChange={(e) => setSpkQuery(e.target.value)}
              placeholder="Find a speaker"
              aria-label="Find a speaker"
              className="mb-1 h-8 w-full rounded-sm border border-border bg-background px-2.5 text-[13px] outline-none focus:border-blue"
            />
            <div role="menu" className="max-h-64 overflow-y-auto">
              {speakers
                .filter((s) => s.name.toLowerCase().includes(spkQuery.trim().toLowerCase()))
                .slice(0, 50)
                .map((s) => (
                  <Option
                    key={s.name}
                    on={filters.speaker?.name === s.name}
                    onClick={() => {
                      set({
                        speaker: filters.speaker?.name === s.name ? null : { name: s.name, ids: s.ids },
                      });
                      close();
                    }}
                  >
                    <span className="flex items-center justify-between gap-2">
                      {s.name}
                      <span className="tabular text-[12px] font-normal text-fg-muted">{count(s.recordings)}</span>
                    </span>
                  </Option>
                ))}
              {!speakers.length && (
                <p className="px-2.5 py-3 text-[13px] text-fg-muted">
                  {speakersLoading ? "Loading speakers…" : "No speakers yet."}
                </p>
              )}
            </div>
          </div>
        )}
      </Chip>
      <Chip
        label={filters.date === "any" ? "Date" : `Date: ${DATE_LABEL[filters.date].toLowerCase()}`}
        active={filters.date !== "any"}
        onClear={() => set({ date: "any" })}
        width={200}
      >
        {(close) => (
          <Radio<DateRange>
            value={filters.date}
            options={DATE_LABEL}
            onChange={(date) => set({ date })}
            close={close}
          />
        )}
      </Chip>
      <Chip
        label={filters.duration === "any" ? "Duration" : `Duration: ${DURATION_LABEL[filters.duration].toLowerCase()}`}
        active={filters.duration !== "any"}
        onClear={() => set({ duration: "any" })}
        width={200}
      >
        {(close) => (
          <Radio<DurationRange>
            value={filters.duration}
            options={DURATION_LABEL}
            onChange={(duration) => set({ duration })}
            close={close}
          />
        )}
      </Chip>
      <Chip
        label={
          filters.languages.length === 1
            ? `Language: ${languageName(filters.languages[0])}`
            : filters.languages.length
              ? `Language: ${filters.languages.length} languages`
              : "Language"
        }
        active={filters.languages.length > 0}
        onClear={() => set({ languages: [] })}
        width={220}
      >
        {() => (
          <div role="menu" aria-label="Language" className="max-h-64 overflow-y-auto">
            {languages.map((l) => {
              const code = l.language ?? "none";
              const on = filters.languages.includes(code);
              return (
                <Option
                  key={code}
                  multi
                  on={on}
                  onClick={() =>
                    set({ languages: on ? filters.languages.filter((x) => x !== code) : [...filters.languages, code] })
                  }
                >
                  <span className="flex min-w-0 flex-1 items-center justify-between gap-2">
                    <span className="truncate">{languageName(l.language)}</span>
                    <span className="tabular text-[12px] font-normal text-fg-muted">{count(l.recordings)}</span>
                  </span>
                </Option>
              );
            })}
            {!languages.length && <p className="px-2.5 py-3 text-[13px] text-fg-muted">No recordings yet.</p>}
          </div>
        )}
      </Chip>
      <Chip
        label={filters.media === "any" ? "Audio / text" : MEDIA_LABEL[filters.media]}
        active={filters.media !== "any"}
        onClear={() => set({ media: "any" })}
        width={200}
      >
        {(close) => (
          <Radio<MediaFilter>
            value={filters.media}
            options={MEDIA_LABEL}
            onChange={(media) => set({ media })}
            close={close}
          />
        )}
      </Chip>
      <Chip
        label={
          filters.tags.length === 1
            ? `Tag: ${filters.tags[0]}`
            : filters.tags.length
              ? `${filters.tags.length} tags`
              : "Tags"
        }
        active={filters.tags.length > 0}
        onClear={() => set({ tags: [] })}
        width={240}
      >
        {() => (
          <div>
            {tags.length > 8 && (
              <input
                value={tagQuery}
                onChange={(e) => setTagQuery(e.target.value)}
                placeholder="Find a tag"
                aria-label="Find a tag"
                className="mb-1 h-8 w-full rounded-sm border border-border bg-background px-2.5 text-[13px] outline-none focus:border-blue"
              />
            )}
            <div role="menu" aria-label="Tags" className="max-h-64 overflow-y-auto">
              {tags
                .filter((t) => t.tag.toLowerCase().includes(tagQuery.trim().toLowerCase()))
                .slice(0, 100)
                .map((t) => (
                  <Option
                    key={t.tag}
                    multi
                    on={tagOn(t.tag)}
                    onClick={() =>
                      set({
                        tags: tagOn(t.tag)
                          ? filters.tags.filter((x) => x.toLowerCase() !== t.tag.toLowerCase())
                          : [...filters.tags, t.tag],
                      })
                    }
                  >
                    <span className="flex min-w-0 flex-1 items-center justify-between gap-2">
                      <span className="truncate">{t.tag}</span>
                      <span className="tabular text-[12px] font-normal text-fg-muted">{count(t.recordings)}</span>
                    </span>
                  </Option>
                ))}
              {!tags.length && (
                <p className="px-2.5 py-3 text-[13px] text-fg-muted">
                  {tagsLoading ? "Loading tags…" : "No tags yet. Select recordings and choose Tag."}
                </p>
              )}
            </div>
          </div>
        )}
      </Chip>
    </>
  );

  if (compact) {
    return (
      <div className="flex flex-col gap-2.5">
        <FilterInput ref={inputRef} value={filters.q} onChange={(q) => set({ q })} />
        <div className="-mx-4 flex gap-1.5 overflow-x-auto px-4 pb-0.5 [scrollbar-width:none]">{chips}</div>
        {trailing && <div className="flex flex-wrap gap-1.5">{trailing}</div>}
      </div>
    );
  }
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <FilterInput ref={inputRef} value={filters.q} onChange={(q) => set({ q })} className="w-[214px]" />
      {chips}
      <span className="flex-1" />
      {trailing}
    </div>
  );
}
