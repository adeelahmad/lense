"use client";

import type { ContentType } from "@/app/openapi-client/types.gen";
import type { FileKind } from "@/components/import/files";
import { useContentTypes } from "@/components/pipelines/catalog-header";
import { Select } from "@/components/ui/field";

export type Base = ContentType["base"];

/** The base type a file of this kind becomes (as the server tells it, content_types.base_of_file): documents and
 * transcripts are text. */
export function baseOf(kind: FileKind): Base {
  return kind === "audio" || kind === "video" || kind === "image" ? kind : "text";
}

/**
 * What the asset is, said as it comes in: one of its base type's content types, so that content type's pipeline runs
 * from the start; or, as before, recognised from the file.
 */
export function ContentTypeSelect({
  base,
  value,
  onChange,
  types,
  id = "import-content-type",
}: {
  base: Base;
  value: string | null;
  onChange: (key: string | null) => void;
  types: ContentType[];
  id?: string;
}) {
  const fits = types.filter((t) => t.base === base);
  const chosen = fits.find((t) => t.key === value);
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
      <label htmlFor={id} className="text-[13px] font-bold text-fg-strong">
        What it is
      </label>
      <Select
        id={id}
        size="sm"
        className="w-[220px]"
        value={chosen ? chosen.key : ""}
        onChange={(e) => onChange(e.target.value || null)}
        options={[
          { value: "", label: "Recognised automatically" },
          ...fits.map((t) => ({ value: t.key, label: t.label })),
        ]}
      />
      <span className="text-[12.5px] text-fg-muted">
        {chosen
          ? `${chosen.description ?? chosen.label}: its pipeline runs.`
          : "Lens tells what it is from its name and type."}
      </span>
    </div>
  );
}

/** ContentTypeSelect with the server's content types (Pipelines → Content types). */
export function ContentTypeField(props: Omit<Parameters<typeof ContentTypeSelect>[0], "types">) {
  const types = useContentTypes();
  if (!types.data) return null;
  return <ContentTypeSelect {...props} types={types.data.types} />;
}
