"use client";

import { ExternalLink, Globe, Lock, SlidersHorizontal } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import type { FieldDef, FieldValues } from "@/app/openapi-client/types.gen";
import {
  changedValues,
  toDraft,
  valueProblem,
  valueText,
  type Draft,
  type Raw,
} from "@/components/fields/fields-model";
import { useFieldValues, useSaveFieldValues, type ValuesSource } from "@/components/fields/use-fields";
import { Button } from "@/components/ui/button";
import { Checkbox, Field, Input, Select, Textarea } from "@/components/ui/field";
import { Label } from "@/components/ui/panel";
import { Skeleton } from "@/components/ui/states";
import { Tooltip } from "@/components/ui/tooltip";

/** The control for one field's value, by its type. */
export function FieldInput({
  field,
  value,
  onChange,
  id,
  describedBy,
  invalid,
}: {
  field: FieldDef;
  value: Raw;
  onChange: (v: Raw) => void;
  id?: string;
  describedBy?: string;
  invalid?: boolean;
}) {
  const text = typeof value === "string" ? value : "";
  switch (field.type) {
    case "longtext":
      return (
        <Textarea
          id={id}
          aria-describedby={describedBy}
          invalid={invalid}
          rows={3}
          value={text}
          onChange={(e) => onChange(e.target.value)}
        />
      );
    case "boolean":
      return (
        <Select
          id={id}
          aria-describedby={describedBy}
          value={value === true ? "yes" : value === false ? "no" : ""}
          onChange={(e) => onChange(e.target.value === "yes" ? true : e.target.value === "no" ? false : null)}
          options={[
            { value: "", label: "—" },
            { value: "yes", label: "Yes" },
            { value: "no", label: "No" },
          ]}
        />
      );
    case "choice":
      return (
        <Select
          id={id}
          aria-describedby={describedBy}
          invalid={invalid}
          value={text}
          onChange={(e) => onChange(e.target.value)}
          options={[{ value: "", label: "—" }, ...(field.options ?? []).map((o) => ({ value: o, label: o }))]}
        />
      );
    case "choices": {
      const picked = Array.isArray(value) ? value : [];
      return (
        <div id={id} role="group" aria-describedby={describedBy} className="flex flex-wrap gap-x-4 gap-y-2 pt-1">
          {(field.options ?? []).map((o) => (
            <Checkbox
              key={o}
              label={o}
              checked={picked.includes(o)}
              onCheckedChange={(on) => onChange(on ? [...picked, o] : picked.filter((x) => x !== o))}
            />
          ))}
        </div>
      );
    }
    default:
      return (
        <Input
          id={id}
          aria-describedby={describedBy}
          invalid={invalid}
          value={text}
          inputMode={field.type === "number" ? "decimal" : undefined}
          type={field.type === "link" ? "url" : "text"}
          placeholder={
            field.type === "date" ? "YYYY, YYYY-MM or YYYY-MM-DD" : field.type === "link" ? "https://" : undefined
          }
          onChange={(e) => onChange(e.target.value)}
        />
      );
  }
}

/** Published or internal, as a small mark beside a field's name. */
function Visibility({ published }: { published: boolean }) {
  return (
    <Tooltip
      content={published ? "Published: shown on public pages and in IIIF" : "Internal: shown only in the workspace"}
    >
      <span
        aria-label={published ? "Published" : "Internal"}
        className="inline-grid size-4 place-items-center text-fg-muted [&_svg]:size-3.5"
      >
        {published ? <Globe /> : <Lock />}
      </span>
    </Tooltip>
  );
}

function shown(f: FieldDef, v: unknown) {
  const text = valueText(f, v);
  if (f.type === "link" && typeof v === "string" && /^https?:\/\//.test(v))
    return (
      <a
        href={v}
        target="_blank"
        rel="noopener noreferrer"
        className="inline-flex items-center gap-1 break-all font-medium text-fg-accent hover:underline"
      >
        {text}
        <ExternalLink className="size-3 shrink-0" aria-hidden />
      </a>
    );
  return <span className={text === "—" ? "text-fg-muted" : "whitespace-pre-wrap break-words"}>{text}</span>;
}

/**
 * An item's custom fields: their values to read, or (for those who may change them) a form that saves only what
 * changed. `title` heads the section; nothing shows when no field describes the item.
 */
export function FieldValuesPanel({
  source,
  title = "Custom fields",
  className,
  onSaved,
}: {
  source: ValuesSource;
  title?: string;
  className?: string;
  onSaved?: () => void;
}) {
  const q = useFieldValues(source);
  const save = useSaveFieldValues(source);
  if (q.isLoading)
    return (
      <div className={className} aria-busy>
        <Skeleton className="h-16 w-full" />
      </div>
    );
  if (q.isError || !q.data || !q.data.fields.length) return null;
  return (
    <section aria-label={title} className={className}>
      <Form
        data={q.data}
        title={title}
        saving={save.isPending}
        onSave={(v) => save.mutate(v, { onSuccess: onSaved })}
      />
    </section>
  );
}

function Form({
  data,
  title,
  saving,
  onSave,
}: {
  data: FieldValues;
  title: string;
  saving: boolean;
  onSave: (values: Record<string, unknown>) => void;
}) {
  const [draft, setDraft] = useState<Draft>(() => toDraft(data.fields));
  // what was saved comes back: start from it again
  useEffect(() => setDraft(toDraft(data.fields)), [data]);
  const changes = useMemo(() => changedValues(data.fields, draft), [data, draft]);
  const problems = Object.fromEntries(
    data.fields.map((x) => [x.field.id, valueProblem(x.field, draft[x.field.id] ?? null)]),
  ) as Record<number, string | null>;
  const bad = Object.values(problems).filter(Boolean).length;
  const dirty = Object.keys(changes).length;
  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center gap-2">
        <SlidersHorizontal className="size-4 text-fg-secondary" aria-hidden />
        <Label as="h3" className="flex-1">
          {title}
        </Label>
      </div>
      {data.can_change ? (
        <form
          className="flex flex-col gap-3"
          onSubmit={(e) => {
            e.preventDefault();
            if (dirty && !bad) onSave(changes);
          }}
        >
          {data.fields.map(({ field }) => (
            <Field
              key={field.id}
              label={
                <span className="inline-flex items-center gap-1.5">
                  {field.label}
                  <Visibility published={field.published} />
                </span>
              }
              hint={problems[field.id] ? undefined : (field.help ?? undefined)}
              error={problems[field.id]}
            >
              {(ids) => (
                <FieldInput
                  field={field}
                  value={draft[field.id] ?? null}
                  onChange={(v) => setDraft((d) => ({ ...d, [field.id]: v }))}
                  id={ids.id}
                  describedBy={ids.describedBy}
                  invalid={ids.invalid}
                />
              )}
            </Field>
          ))}
          <div className="flex items-center justify-end gap-2">
            {dirty > 0 && (
              <Button type="button" size="sm" variant="ghost" onClick={() => setDraft(toDraft(data.fields))}>
                Discard
              </Button>
            )}
            <Button
              type="submit"
              size="sm"
              variant="primary"
              disabled={!dirty || bad > 0 || saving}
              disabledReason={bad ? "Fix the fields marked in red first" : !dirty ? "Nothing has changed" : undefined}
            >
              {saving ? "Saving…" : dirty > 1 ? `Save ${dirty} fields` : "Save"}
            </Button>
          </div>
        </form>
      ) : (
        <dl className="flex flex-col gap-2.5">
          {data.fields.map(({ field, value }) => (
            <div key={field.id} className="flex flex-col gap-0.5">
              <dt className="inline-flex items-center gap-1.5 text-[12px] font-semibold text-fg-muted">
                {field.label}
                <Visibility published={field.published} />
              </dt>
              <dd className="text-[13.5px] leading-[1.45] text-fg">{shown(field, value)}</dd>
            </div>
          ))}
        </dl>
      )}
    </div>
  );
}
