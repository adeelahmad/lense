"use client";

import { useId, useMemo } from "react";

import { languageName } from "@/components/library/model";
import { useLanguages, useOrigins, useTagCounts } from "@/components/library/use-library";
import { DAY_RX, type SearchFilters } from "@/components/search/query";
import { Input, Select, type Option } from "@/components/ui/field";
import { count } from "@/lib/format";

type Key = "from" | "to" | "tag" | "origin" | "language";

/** The names the asset filters' chips show: a source's name, a language's name. */
export function useAssetFilterLabels(filters: SearchFilters): Partial<Record<Key, string>> {
  const ns = filters.namespace ?? null;
  const origins = useOrigins(ns);
  const { from, to, tag, origin, language } = filters;
  return useMemo(
    () => ({
      ...(from ? { from } : {}),
      ...(to ? { to } : {}),
      ...(tag ? { tag } : {}),
      ...(origin ? { origin: origins.data?.find((o) => o.origin === origin)?.name ?? origin } : {}),
      ...(language ? { language: languageName(language) } : {}),
    }),
    [from, to, tag, origin, language, origins.data],
  );
}

/** Options with "Any …" first, the counted values, and the chosen one even when it's no longer among them. */
function options(any: string, values: { value: string; label: string; n: number }[], chosen?: string): Option[] {
  const out: Option[] = [
    { value: "", label: any },
    ...values.map((v) => ({ value: v.value, label: `${v.label} (${count(v.n)})` })),
  ];
  if (chosen && !values.some((v) => v.value === chosen)) out.push({ value: chosen, label: chosen });
  return out;
}

/**
 * When the assets were recorded, their tag, where they came from and their language: filters on the assets the moments
 * are in, as the Library has them. Each is empty until picked, and picking one searches again; a list with nothing to
 * choose between isn't shown.
 */
export function AssetFilters({
  filters,
  onChange,
}: {
  filters: SearchFilters;
  onChange: (key: Key, value: string | undefined) => void;
}) {
  const id = useId();
  const ns = filters.namespace ?? null;
  const tags = useTagCounts(ns);
  const origins = useOrigins(ns);
  const languages = useLanguages(ns);
  const tagValues = (tags.data ?? []).map((t) => ({ value: t.tag, label: t.tag, n: t.recordings }));
  const originValues = (origins.data ?? []).map((o) => ({ value: o.origin, label: o.name, n: o.recordings }));
  const languageValues = (languages.data ?? []).map((l) => ({
    value: l.language ?? "none",
    label: languageName(l.language),
    n: l.recordings,
  }));
  const pick = (key: Key) => (e: React.ChangeEvent<HTMLSelectElement | HTMLInputElement>) => {
    const v = e.target.value;
    if (!v || (key !== "from" && key !== "to") || DAY_RX.test(v)) onChange(key, v || undefined);
  };
  const date = "h-8 px-2 text-[13px]";
  return (
    <section aria-labelledby={`${id}-assets`} className="flex flex-col gap-1.5">
      <h3 id={`${id}-assets`} className="px-1.5 pb-1 label-caps">
        Assets
      </h3>
      <div className="grid grid-cols-[34px_minmax(0,1fr)] items-center gap-x-1.5 gap-y-1.5 px-1.5 text-[12.5px] text-fg-secondary">
        <label htmlFor={`${id}-from`}>From</label>
        <Input
          id={`${id}-from`}
          type="date"
          title="Recorded on or after this day"
          className={date}
          value={filters.from ?? ""}
          max={filters.to}
          onChange={pick("from")}
        />
        <label htmlFor={`${id}-to`}>To</label>
        <Input
          id={`${id}-to`}
          type="date"
          title="Recorded on or before this day"
          className={date}
          value={filters.to ?? ""}
          min={filters.from}
          onChange={pick("to")}
        />
      </div>
      {(tagValues.length > 0 || filters.tag) && (
        <div className="px-1.5">
          <Select
            size="sm"
            aria-label="Tag"
            value={filters.tag ?? ""}
            options={options("Any tag", tagValues, filters.tag)}
            onChange={pick("tag")}
          />
        </div>
      )}
      {(originValues.length > 1 || filters.origin) && (
        <div className="px-1.5">
          <Select
            size="sm"
            aria-label="Source"
            value={filters.origin ?? ""}
            options={options("Any source", originValues, filters.origin)}
            onChange={pick("origin")}
          />
        </div>
      )}
      {(languageValues.length > 1 || filters.language) && (
        <div className="px-1.5">
          <Select
            size="sm"
            aria-label="Language"
            value={filters.language ?? ""}
            options={options("Any language", languageValues, filters.language)}
            onChange={pick("language")}
          />
        </div>
      )}
    </section>
  );
}
