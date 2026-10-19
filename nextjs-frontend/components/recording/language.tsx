"use client";

import { Pencil } from "lucide-react";
import { useState } from "react";

import { browserLanguages } from "@/components/recording/files-tab";
import { useSetLanguage } from "@/components/recording/hooks";
import { knownLanguage, languageEdit, languageLine } from "@/components/recording/language-model";
import { languageChoices } from "@/components/recording/translate-model";
import { Button, IconButton } from "@/components/ui/button";
import { Input } from "@/components/ui/field";

/** The asset's language by name and tag, detected or set by a person. Editors change it in place: the pencil opens a
 * field with the tag selected (the browser's languages and common ones offered), Enter saves, Escape keeps it, and
 * an empty field forgets it so the next transcription says again. */
export function LanguageEditor({
  rid,
  language,
  set,
  editable,
}: {
  rid: number;
  language: string | null | undefined;
  set: boolean;
  editable: boolean;
}) {
  const save = useSetLanguage(rid);
  const [draft, setDraft] = useState<string | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const line = languageLine(language);
  const shown = line ? `${line}${set ? " · set by a person" : " · detected"}` : "Not known";
  if (!editable) return <>{shown}</>;
  if (draft == null)
    return (
      <span className="flex items-center gap-1">
        {shown}
        <IconButton label="Change the language" size={26} onClick={() => setDraft(knownLanguage(language) ?? "")}>
          <Pencil />
        </IconButton>
      </span>
    );
  const submit = () => {
    const edit = languageEdit(draft, language);
    if (edit === "same") return setDraft(null);
    if ("problem" in edit) return setProblem(edit.problem);
    save.mutate(edit.language, { onSuccess: () => setDraft(null) });
  };
  return (
    <form
      className="flex flex-wrap items-center gap-2"
      onSubmit={(e) => {
        e.preventDefault();
        submit();
      }}
    >
      <Input
        aria-label="Language"
        className="h-8 w-[160px]"
        value={draft}
        placeholder="en-GB"
        list="asset-languages"
        autoFocus
        invalid={!!problem}
        onFocus={(e) => e.currentTarget.select()}
        onChange={(e) => {
          setDraft(e.target.value);
          setProblem(null);
        }}
        onKeyDown={(e) => e.key === "Escape" && setDraft(null)}
      />
      <datalist id="asset-languages">
        {languageChoices(browserLanguages(), null).map((l) => (
          <option key={l.code} value={l.code}>
            {l.name}
          </option>
        ))}
      </datalist>
      <Button type="submit" size="sm" disabled={save.isPending}>
        {save.isPending ? "Saving…" : "Save"}
      </Button>
      <Button type="button" size="sm" variant="ghost" onClick={() => setDraft(null)}>
        Cancel
      </Button>
      {problem && (
        <p role="alert" className="w-full text-[12.5px] text-red-dark">
          {problem}
        </p>
      )}
    </form>
  );
}
