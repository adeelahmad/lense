"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ArrowDown, ArrowUp, FileText } from "lucide-react";
import Link from "next/link";
import { useEffect, useMemo, useState } from "react";

import { Metadata } from "@/app/openapi-client";
import { isUnreachable } from "@/components/errors/error-states";
import { useCollectionItems } from "@/components/iiif/collection-data";
import { ACCESS, FIELD_LABEL, FIELD_MAPS, isEmpty, LANG_RX, same, type Field, type Meta } from "@/components/iiif/metadata-model";
import { keys, useNamespaceMeta, type NamespaceProfile } from "@/components/iiif/queries";
import { RIGHTS, rightsShort } from "@/components/iiif/rights";
import { Banner } from "@/components/ui/banner";
import { Button } from "@/components/ui/button";
import { Input, Select, Switch } from "@/components/ui/field";
import { EmptyState, SkeletonRows } from "@/components/ui/states";
import { useToast } from "@/components/ui/toast";
import { data, useApiClient } from "@/lib/api/browser";
import { needRole, useArchive } from "@/lib/hooks/session";
import { cn } from "@/lib/utils";

/** Fields a profile covers, in the editor's default order (access has its own default below). */
const PROFILE_FIELDS: Field[] = ["label", "summary", "navDate", "creators", "contributors", "subjects", "language", "rights", "attribution", "provider", "identifiers", "homepage", "metadata", "related"];

/** Where a field's value comes from when the profile sets no default. */
const DERIVED: Partial<Record<Field, string>> = {
  label: "recording title",
  summary: "from Summarize",
  navDate: "recording date",
  contributors: "speakers",
  subjects: "top entities",
  language: "detected by Transcribe",
};

/** Profile defaults this page can edit, as simple text. */
const EDITABLE_DEFAULTS: Field[] = ["rights", "attribution", "provider", "language", "homepage"];

type Draft = { required: Field[]; order: Field[]; defaults: Meta; vocabularies: { subjects: string[]; language: string[] }; default_access: string };

function fromProfile(p: NamespaceProfile | undefined): Draft {
  const order = ((p?.order?.length ? p.order : PROFILE_FIELDS) as Field[]).filter((f) => PROFILE_FIELDS.includes(f));
  return {
    required: (p?.required ?? []) as Field[],
    order: [...order, ...PROFILE_FIELDS.filter((f) => !order.includes(f))],
    defaults: (p?.defaults ?? {}) as Meta,
    vocabularies: { subjects: p?.vocabularies?.subjects ?? [], language: p?.vocabularies?.language ?? [] },
    default_access: p?.default_access ?? "private",
  };
}

function defaultText(f: Field, d: Meta): string {
  const v = d[f];
  if (isEmpty(v)) return "";
  if (f === "rights") return v as string;
  if (f === "attribution") return Object.values(v as Record<string, string[]>)[0]?.[0] ?? "";
  if (f === "provider") return (v as { name?: string }).name ?? "";
  if (f === "language") return (v as string[]).join(", ");
  if (f === "homepage") return v as string;
  return "";
}

function setDefault(f: Field, text: string, d: Meta): Meta {
  const t = text.trim();
  const next = { ...d } as Record<string, unknown>;
  if (!t) delete next[f];
  else if (f === "attribution") next[f] = { none: [text] };
  else if (f === "provider") next[f] = { ...((d.provider as object) ?? {}), name: text };
  else if (f === "language") next[f] = t.split(/[\s,]+/).filter(Boolean);
  else next[f] = t;
  return next as Meta;
}

function defaultError(f: Field, d: Meta): string | null {
  const v = d[f];
  if (isEmpty(v)) return null;
  if (f === "language" && (v as string[]).some((c) => !LANG_RX.test(c) || c === "none")) return "Use codes such as en, pt-BR";
  if (f === "homepage" && !/^https?:\/\/\S+$/.test(v as string)) return "Use an http(s) address";
  return null;
}

/** A namespace's metadata profile (MD3): field order, required fields, defaults, vocabularies and default access. */
export function ProfileEditor({ ns }: { ns: string }) {
  const client = useApiClient();
  const qc = useQueryClient();
  const toast = useToast();
  const { can, namespaces } = useArchive();
  const nsMeta = useNamespaceMeta(ns);
  const [draft, setDraft] = useState<Draft | null>(null);
  const loaded = useMemo(() => fromProfile(nsMeta.data?.profile), [nsMeta.data]);
  useEffect(() => {
    if (nsMeta.data) setDraft(fromProfile(nsMeta.data.profile));
  }, [nsMeta.data]);
  const { items } = useCollectionItems(ns, 0, nsMeta.data?.profile);
  const isOwner = can("owner", ns);

  const save = useMutation({
    mutationFn: (d: Draft) =>
      data(
        Metadata.updateNamespaceMetadata({
          client,
          path: { name: ns },
          body: {
            profile: {
              required: d.required,
              order: d.order,
              defaults: Object.fromEntries(Object.entries(d.defaults).filter(([, v]) => !isEmpty(v))),
              vocabularies: Object.fromEntries(Object.entries(d.vocabularies).filter(([, v]) => v.length)),
              default_access: d.default_access,
            },
          },
        }),
      ),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: keys.namespace(ns) });
      void qc.invalidateQueries({ queryKey: ["metadata"] });
      toast({ title: "Profile saved", body: "Recordings that now miss a required field show it as a problem; nothing was unpublished.", tone: "green" });
    },
  });

  if (namespaces.length && !namespaces.some((n) => n.name === ns))
    return (
      <EmptyState icon={<FileText />} title="This namespace doesn’t exist" actions={<Button asChild><Link href="/iiif">All collections</Link></Button>}>
        Or you don’t have a role in it. Namespaces you don’t belong to are never shown here.
      </EmptyState>
    );
  if (nsMeta.isError)
    return (
      <EmptyState tone="error" icon={<FileText />} title={isUnreachable(nsMeta.error) ? "Can’t reach the server" : "Couldn’t load the profile"} actions={<Button onClick={() => nsMeta.refetch()}>Try again</Button>}>
        {nsMeta.error.message}
      </EmptyState>
    );

  const d = draft;
  const changed = d ? !same(d, loaded) : false;
  const errors = d ? Object.fromEntries(EDITABLE_DEFAULTS.map((f) => [f, defaultError(f, d.defaults)]).filter(([, e]) => e)) : {};
  const newlyRequired = d ? d.required.filter((f) => !loaded.required.includes(f)) : [];
  const published = items.filter((i) => i.meta && i.meta.meta.access && i.meta.meta.access !== "private");
  const affected = (f: Field) => published.filter((i) => isEmpty(i.meta?.meta[f])).length;
  const up = (patch: Partial<Draft>) => setDraft((x) => (x ? { ...x, ...patch } : x));
  const move = (i: number, dir: number) => {
    if (!d) return;
    const j = i + dir;
    if (j < 0 || j >= d.order.length) return;
    const order = [...d.order];
    [order[i], order[j]] = [order[j], order[i]];
    up({ order });
  };

  return (
    <div className="px-4 py-5 sm:px-6">
      <section className="flex flex-col gap-3 rounded-md border border-border bg-background px-4 py-5 sm:px-6">
        <div className="flex flex-wrap items-center gap-2.5">
          <div className="flex min-w-0 flex-1 flex-col gap-1">
            <Link href={`/iiif/collections/${ns}`} className="text-[12px] font-medium text-fg-muted hover:underline">
              Collections · {ns}
            </Link>
            <h1 className="text-[20px] font-bold leading-tight text-fg">Metadata profile · {ns}</h1>
          </div>
          <code className="font-mono text-[12px] text-fg-muted">applies to {ns}</code>
          <Button size="sm" disabled disabledReason="Fields are fixed by the server; add your own as label / value pair defaults">
            Add field
          </Button>
        </div>
        <p className="text-[13px] leading-[1.45] text-fg-secondary">
          Field order here is the order in the editor and in viewers’ metadata panels. Changing a required field flags existing recordings that miss it; it doesn’t unpublish them.
        </p>
        {!isOwner && <Banner tone="info">{needRole("owner", ns)}. You can read the profile.</Banner>}
        {!d ? (
          <SkeletonRows rows={6} />
        ) : (
          <>
            <div className="relative overflow-x-auto rounded-md border border-border">
              <table className="w-full min-w-[820px] border-collapse text-[13px]" aria-label="Profile fields">
                <thead className="bg-surface text-left">
                  <tr>
                    <th scope="col" className="w-[52px] px-3 py-2.5">
                      <span className="sr-only">Order</span>
                    </th>
                    {["Field", "Maps to", "Required", "Default", "Vocabulary"].map((h) => (
                      <th key={h} scope="col" className="px-3 py-2.5 text-[12px] font-semibold text-fg-secondary">
                        {h}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {d.order.map((f, i) => {
                    const req = d.required.includes(f);
                    const editable = EDITABLE_DEFAULTS.includes(f);
                    return (
                      <tr key={f} className="h-11 border-t border-border">
                        <td className="px-3">
                          <span className="flex gap-0.5">
                            <button type="button" aria-label={`Move ${FIELD_LABEL[f]} up`} disabled={!isOwner || i === 0} onClick={() => move(i, -1)} className="grid size-6 place-items-center rounded-full text-fg-muted hover:bg-surface-neutral disabled:opacity-30">
                              <ArrowUp className="size-3.5" />
                            </button>
                            <button type="button" aria-label={`Move ${FIELD_LABEL[f]} down`} disabled={!isOwner || i === d.order.length - 1} onClick={() => move(i, 1)} className="grid size-6 place-items-center rounded-full text-fg-muted hover:bg-surface-neutral disabled:opacity-30">
                              <ArrowDown className="size-3.5" />
                            </button>
                          </span>
                        </td>
                        <td className="px-3 font-semibold">{FIELD_LABEL[f]}</td>
                        <td className="px-3">
                          <code className="font-mono text-[11.5px] text-fg-secondary">{FIELD_MAPS[f] ?? f}</code>
                        </td>
                        <td className="px-3">
                          <Switch
                            aria-label={`${FIELD_LABEL[f]} is required`}
                            checked={req}
                            disabled={!isOwner}
                            onCheckedChange={(on) => up({ required: on ? [...d.required, f] : d.required.filter((x) => x !== f) })}
                          />
                        </td>
                        <td className="px-3 py-1.5 text-fg-secondary">
                          {editable && isOwner ? (
                            f === "rights" ? (
                              <Select
                                aria-label="Default rights"
                                size="sm"
                                value={(d.defaults.rights as string) ?? ""}
                                onChange={(e) => up({ defaults: setDefault(f, e.target.value, d.defaults) })}
                                options={[{ value: "", label: DERIVED[f] ?? "—" }, ...RIGHTS.map((r) => ({ value: r.uri, label: r.code }))]}
                              />
                            ) : (
                              <Input
                                aria-label={`Default ${FIELD_LABEL[f].toLowerCase()}`}
                                value={defaultText(f, d.defaults)}
                                placeholder={DERIVED[f] ?? "—"}
                                invalid={Boolean(errors[f])}
                                title={errors[f] ?? undefined}
                                onChange={(e) => up({ defaults: setDefault(f, e.target.value, d.defaults) })}
                                className="h-8 text-[13px]"
                              />
                            )
                          ) : f === "rights" && d.defaults.rights ? (
                            rightsShort(d.defaults.rights as string)
                          ) : (
                            defaultText(f, d.defaults) || DERIVED[f] || "—"
                          )}
                        </td>
                        <td className="px-3 py-1.5 text-fg-secondary">
                          {f === "subjects" || f === "language" ? (
                            <Input
                              aria-label={`${FIELD_LABEL[f]} vocabulary`}
                              value={d.vocabularies[f].join(", ")}
                              readOnly={!isOwner}
                              placeholder={f === "subjects" ? "entities, or a list of terms" : "any language"}
                              onChange={(e) => up({ vocabularies: { ...d.vocabularies, [f]: e.target.value.split(",").map((x) => x.trim()).filter(Boolean) } })}
                              className="h-8 text-[13px]"
                            />
                          ) : f === "rights" ? (
                            "CC · RightsStatements.org"
                          ) : f === "navDate" ? (
                            "ISO 8601"
                          ) : (
                            "—"
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            <div className="grid items-center gap-2 sm:grid-cols-[180px_220px_minmax(0,1fr)]">
              <span className="text-[13px] font-bold text-fg-strong">Default access</span>
              <Select
                aria-label="Default access"
                value={d.default_access}
                disabled={!isOwner}
                onChange={(e) => up({ default_access: e.target.value })}
                options={ACCESS.map((a) => ({ value: a.value, label: a.label }))}
              />
              <span className="text-[12.5px] leading-[1.35] text-fg-secondary">{ACCESS.find((a) => a.value === d.default_access)?.anon} Applies to recordings without their own access.</span>
            </div>
            {newlyRequired.map((f) =>
              affected(f) ? (
                <div key={f} className="flex gap-2 rounded-[10px] border border-gold-border bg-gold-surface px-3 py-2.5 text-[13px] leading-[1.4]">
                  <span aria-hidden className="font-extrabold text-gold-dark">
                    ◆
                  </span>
                  <span>
                    Making “{FIELD_LABEL[f]}” required affects <b>{affected(f)} published recording{affected(f) === 1 ? "" : "s"}</b> that don’t have it. They’ll show “needs attention” until filled.
                  </span>
                </div>
              ) : null,
            )}
            {save.isError && <Banner tone="error">{save.error.message}</Banner>}
            <div className={cn("flex items-center gap-2", !changed && "invisible")} aria-hidden={!changed}>
              <span className="flex-1 text-[13px] font-medium">Unsaved changes to the profile</span>
              <Button variant="ghost" size="sm" onClick={() => setDraft(loaded)}>
                Discard
              </Button>
              <Button variant="primary" size="sm" disabled={!isOwner || save.isPending || Object.keys(errors).length > 0} disabledReason={!isOwner ? needRole("owner", ns) : undefined} onClick={() => save.mutate(d)}>
                {save.isPending ? "Saving…" : "Save profile"}
              </Button>
            </div>
          </>
        )}
      </section>
    </div>
  );
}
