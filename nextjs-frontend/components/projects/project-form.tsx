"use client";

import { Checkbox, Field, Input, Textarea } from "@/components/ui/field";
import { useArchive } from "@/lib/hooks/session";

export type ProjectDraft = { name: string; goal: string; instructions: string; namespaces: string[] };

/** A new project's draft: the namespace picked in the top bar, or the only one there is. */
export function useNewDraft(): ProjectDraft {
  const { namespace, namespaces } = useArchive();
  const ns = namespace ?? (namespaces.length === 1 ? namespaces[0].name : null);
  return { name: "", goal: "", instructions: "", namespaces: ns ? [ns] : [] };
}

/** The namespaces a project draws on: every one you can read, picked with a box each (no picker with just one). */
function NamespaceBoxes({ value, onChange }: { value: string[]; onChange: (v: string[]) => void }) {
  const { namespaces } = useArchive();
  if (namespaces.length <= 1 && value.length === 1 && namespaces[0]?.name === value[0]) return null;
  const toggle = (name: string, on: boolean) => onChange(on ? [...value, name] : value.filter((n) => n !== name));
  return (
    <fieldset className="m-0 flex flex-col gap-1.5 border-0 p-0">
      <legend className="mb-1.5 text-[13px] font-bold leading-tight text-fg-strong">Namespaces</legend>
      <div className="flex flex-wrap gap-x-5 gap-y-2">
        {namespaces.map((n) => (
          <Checkbox
            key={n.id}
            label={n.name}
            checked={value.includes(n.name)}
            onCheckedChange={(on) => toggle(n.name, on)}
          />
        ))}
      </div>
      <p className="m-0 text-[12.5px] leading-snug text-fg-muted">
        What the assistant draws on in every conversation of the project.
      </p>
    </fieldset>
  );
}

/** Name, goal, namespaces and, when editing, the instructions. The name has the focus; Enter in it saves. */
export function ProjectFields({
  draft,
  onChange,
  instructions,
}: {
  draft: ProjectDraft;
  onChange: (d: ProjectDraft) => void;
  instructions?: boolean;
}) {
  return (
    <>
      <Field label="Name">
        {(f) => (
          <Input
            id={f.id}
            data-autofocus
            value={draft.name}
            maxLength={120}
            placeholder="e.g. Build a treehouse with Sam"
            onChange={(e) => onChange({ ...draft, name: e.target.value })}
          />
        )}
      </Field>
      <Field label="Goal" optional hint="What it's for, in your words. Every answer in the project reads it.">
        {(f) => (
          <Textarea
            id={f.id}
            aria-describedby={f.describedBy}
            rows={3}
            value={draft.goal}
            maxLength={4000}
            onChange={(e) => onChange({ ...draft, goal: e.target.value })}
          />
        )}
      </Field>
      {instructions && (
        <Field label="Instructions" optional hint="How the assistant should work in it.">
          {(f) => (
            <Textarea
              id={f.id}
              aria-describedby={f.describedBy}
              rows={3}
              value={draft.instructions}
              maxLength={4000}
              onChange={(e) => onChange({ ...draft, instructions: e.target.value })}
            />
          )}
        </Field>
      )}
      <NamespaceBoxes value={draft.namespaces} onChange={(namespaces) => onChange({ ...draft, namespaces })} />
    </>
  );
}
