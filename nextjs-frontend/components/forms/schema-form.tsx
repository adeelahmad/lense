"use client";

/**
 * A form generated from a JSON Schema object: text, template, number, choice, toggle, list and JSON fields, with the
 * schema's titles as labels and its descriptions as hints. Typing `{{` in a field that takes a template opens the
 * variable picker when `variables` are given; `preview` puts a line under a field (what its template gives).
 */
import { useEffect, useId, useMemo, useRef, useState, type KeyboardEvent, type ReactNode } from "react";

import { Checkbox, Field, Input, Select, Textarea } from "@/components/ui/field";
import { cn } from "@/lib/utils";

import {
  fieldProblem,
  fieldsOf,
  fromText,
  insertVariable,
  openVariable,
  setValue,
  toText,
  type JsonSchema,
  type SchemaField,
} from "./schema-model";

export type Variable = { path: string; label?: string; group?: string };

export function SchemaForm({
  schema,
  required,
  value,
  onChange,
  variables,
  preview,
  errors,
  idPrefix,
  className,
}: {
  schema: JsonSchema | undefined;
  required?: string[];
  value: Record<string, unknown>;
  onChange: (v: Record<string, unknown>) => void;
  variables?: Variable[];
  preview?: (key: string, v: unknown) => ReactNode;
  /** Problems by field key, from the API. */
  errors?: Record<string, string>;
  idPrefix?: string;
  className?: string;
}) {
  const fields = useMemo(() => fieldsOf(schema, required), [schema, required]);
  const auto = useId();
  const prefix = idPrefix ?? auto;
  if (!fields.length) return <p className="text-[13px] text-fg-muted">Nothing to set.</p>;
  return (
    <div className={cn("flex flex-col gap-4", className)}>
      {fields.map((f) => (
        <SchemaInput
          key={f.key}
          id={`${prefix}-${f.key}`}
          field={f}
          value={value[f.key]}
          onChange={(v) => onChange(setValue(value, f.key, v))}
          variables={f.template ? variables : undefined}
          preview={preview}
          error={errors?.[f.key]}
        />
      ))}
    </div>
  );
}

function SchemaInput({
  id,
  field: f,
  value,
  onChange,
  variables,
  preview,
  error,
}: {
  id: string;
  field: SchemaField;
  value: unknown;
  onChange: (v: unknown) => void;
  variables?: Variable[];
  preview?: (key: string, v: unknown) => ReactNode;
  error?: string;
}) {
  const [text, setText] = useState(() => toText(f, value));
  const [touched, setTouched] = useState(false);
  // a value set from outside (a draft filled in, undo) shows; what's being typed stays as typed
  useEffect(() => {
    if (JSON.stringify(fromText(f, text)) !== JSON.stringify(value)) setText(toText(f, value));
  }, [value]);
  const problem = error || (touched ? fieldProblem(f, fromText(f, text)) : "");
  const placeholder = f.default !== undefined ? toText(f, f.default) : undefined;
  const hint = (
    <>
      {f.help}
      {preview && value !== undefined && <span className="mt-1 block text-fg-secondary">{preview(f.key, value)}</span>}
    </>
  );
  if (f.kind === "toggle")
    return (
      <div className="flex flex-col gap-1">
        <Checkbox
          checked={value === undefined ? Boolean(f.default) : Boolean(value)}
          onCheckedChange={(v) => onChange(v)}
          label={f.label}
        />
        {f.help && <p className="text-[12.5px] text-fg-muted">{f.help}</p>}
      </div>
    );
  return (
    <Field
      label={f.label}
      optional={!f.required}
      hint={f.help || (preview && value !== undefined) ? hint : undefined}
      error={problem || undefined}
      htmlFor={id}
    >
      {({ id: fid, describedBy, invalid }) =>
        f.kind === "choice" ? (
          <Select
            id={fid}
            aria-describedby={describedBy}
            invalid={invalid}
            value={value === undefined ? "" : String(value)}
            onChange={(e) => onChange(e.target.value === "" ? undefined : e.target.value)}
            options={[
              { value: "", label: placeholder ? `${placeholder} (default)` : "—" },
              ...(f.options ?? []).map((o) => ({ value: o, label: o })),
            ]}
          />
        ) : (
          <TemplateBox
            id={fid}
            describedBy={describedBy}
            invalid={invalid}
            multiline={f.kind === "long" || f.kind === "json" || f.kind === "list"}
            mono={f.kind === "json"}
            text={text}
            placeholder={placeholder}
            variables={variables}
            onBlur={() => setTouched(true)}
            onText={(t) => {
              setText(t);
              onChange(fromText(f, t));
            }}
          />
        )
      }
    </Field>
  );
}

/** A text box that opens the variable picker on `{{`: arrows move, Enter or a click puts the variable in, Esc closes. */
export function TemplateBox({
  id,
  describedBy,
  invalid,
  multiline,
  mono,
  text,
  placeholder,
  variables,
  onText,
  onBlur,
}: {
  id: string;
  describedBy?: string;
  invalid?: boolean;
  multiline?: boolean;
  mono?: boolean;
  text: string;
  placeholder?: string;
  variables?: Variable[];
  onText: (t: string) => void;
  onBlur?: () => void;
}) {
  const ref = useRef<HTMLInputElement & HTMLTextAreaElement>(null);
  const [open, setOpen] = useState<{ start: number; query: string } | null>(null);
  const [active, setActive] = useState(0);
  const listId = `${id}-vars`;
  const shown = useMemo(() => {
    if (!open || !variables?.length) return [];
    const q = open.query.toLowerCase();
    return variables
      .filter((v) => !q || v.path.toLowerCase().includes(q) || v.label?.toLowerCase().includes(q))
      .slice(0, 12);
  }, [open, variables]);
  const look = (t: string, caret: number | null) => {
    const at = variables?.length && caret != null ? openVariable(t, caret) : null;
    setOpen(at);
    setActive(0);
  };
  const pick = (v: Variable) => {
    const el = ref.current;
    const caret = el?.selectionStart ?? text.length;
    const next = insertVariable(text, caret, v.path);
    onText(next.text);
    setOpen(null);
    requestAnimationFrame(() => {
      el?.focus();
      el?.setSelectionRange(next.caret, next.caret);
    });
  };
  const onKeyDown = (e: KeyboardEvent) => {
    if (!shown.length) return;
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      setActive((a) => (a + (e.key === "ArrowDown" ? 1 : shown.length - 1)) % shown.length);
    } else if (e.key === "Enter" || e.key === "Tab") {
      e.preventDefault();
      pick(shown[active]);
    } else if (e.key === "Escape") {
      e.preventDefault();
      e.stopPropagation();
      setOpen(null);
    }
  };
  const common = {
    id,
    ref,
    value: text,
    placeholder,
    invalid,
    mono,
    role: variables?.length ? "combobox" : undefined,
    "aria-describedby": describedBy,
    "aria-expanded": variables?.length ? shown.length > 0 : undefined,
    "aria-controls": shown.length ? listId : undefined,
    "aria-autocomplete": variables?.length ? ("list" as const) : undefined,
    onKeyDown,
    onBlur: () => {
      onBlur?.();
      setOpen(null);
    },
    onChange: (e: { target: { value: string; selectionStart: number | null } }) => {
      onText(e.target.value);
      look(e.target.value, e.target.selectionStart);
    },
  };
  return (
    <div className="relative">
      {multiline ? <Textarea rows={3} {...common} /> : <Input {...common} />}
      {shown.length > 0 && (
        <ul
          id={listId}
          role="listbox"
          aria-label="Variables"
          className="absolute left-0 right-0 top-full z-20 mt-1 max-h-64 overflow-auto rounded-sm border border-border bg-background py-1 shadow-lg"
        >
          {shown.map((v, k) => (
            <li
              key={v.path}
              role="option"
              aria-selected={k === active}
              // mousedown, so the box keeps focus and doesn't close the list first
              onMouseDown={(e) => {
                e.preventDefault();
                pick(v);
              }}
              className={cn("cursor-pointer px-3 py-1.5 text-[13px]", k === active && "bg-surface")}
            >
              <span className="font-mono">{v.path}</span>
              {v.label && <span className="ml-2 text-fg-muted">{v.label}</span>}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
