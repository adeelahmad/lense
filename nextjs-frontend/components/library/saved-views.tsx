"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Bookmark, BookmarkPlus, Check } from "lucide-react";
import { useEffect, useState } from "react";

import { Views } from "@/app/openapi-client";
import type { SavedView, ViewState } from "@/app/openapi-client/types.gen";
import { describeView, groupViews, isShowing, viewMeta, whyNoShare } from "@/components/library/views-model";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { Checkbox, Field, Input } from "@/components/ui/field";
import { useToast } from "@/components/ui/toast";
import { data, useApiClient } from "@/lib/api/browser";
import { needRole, useArchive } from "@/lib/hooks/session";

/** Your saved views and the ones shared with namespaces you can read. */
export function useViews() {
  const client = useApiClient();
  return useQuery({
    queryKey: ["views"],
    queryFn: () => data(Views.listViews({ client })),
    staleTime: 30_000,
  });
}

function SaveViewDialog({
  open,
  onOpenChange,
  state,
  namespace,
  originName,
  collectionName,
  fieldName,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  state: ViewState;
  namespace: string | null;
  originName: (key: string) => string;
  collectionName: (id: number) => string | null;
  fieldName: (id: number) => string | null;
}) {
  const client = useApiClient();
  const qc = useQueryClient();
  const toast = useToast();
  const { can } = useArchive();
  const [name, setName] = useState("");
  const [shared, setShared] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    if (!open) return;
    setName("");
    setShared(false);
    setError(null);
  }, [open]);
  const noShare = whyNoShare(namespace, Boolean(namespace) && can("editor", namespace), needRole("editor", namespace));
  const save = useMutation({
    mutationFn: () =>
      data(Views.createView({ client, body: { name: name.trim(), namespace, shared: shared && !noShare, state } })),
    onSuccess: (v) => {
      void qc.invalidateQueries({ queryKey: ["views"] });
      onOpenChange(false);
      toast({
        title: "View saved",
        body: v.shared ? `Everyone in ${v.namespace} can open “${v.name}” from Views.` : `Open “${v.name}” from Views.`,
      });
    },
    onError: (e: Error) => setError(e.message),
  });
  const go = () => name.trim() && !save.isPending && save.mutate();
  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      title="Save view"
      description={`${namespace ?? "All namespaces"} · ${describeView(state, originName, collectionName, fieldName)}`}
      actions={
        <>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button variant="primary" disabled={!name.trim() || save.isPending} onClick={go}>
            {save.isPending ? "Saving…" : "Save view"}
          </Button>
        </>
      }
    >
      <Field label="Name" error={error ?? undefined}>
        {({ id, describedBy, invalid }) => (
          <Input
            id={id}
            aria-describedby={describedBy}
            invalid={invalid}
            value={name}
            maxLength={60}
            autoFocus
            onChange={(e) => {
              setName(e.target.value);
              setError(null);
            }}
            onKeyDown={(e) => e.key === "Enter" && go()}
          />
        )}
      </Field>
      <div className="flex flex-col gap-1">
        <Checkbox
          checked={shared && !noShare}
          disabled={Boolean(noShare)}
          onCheckedChange={setShared}
          label={`Share with ${namespace ?? "its namespace"}`}
        />
        <span className="pl-7 text-[12.5px] text-fg-muted">
          {noShare ?? "Everyone with a role there sees it in Views; only you can change it."}
        </span>
      </div>
    </Dialog>
  );
}

function ViewRow({
  view: v,
  showing,
  current,
  namespace,
  onApply,
  originName,
  collectionName,
  fieldName,
}: {
  view: SavedView;
  showing: boolean;
  current: ViewState;
  namespace: string | null;
  onApply: () => void;
  originName: (key: string) => string;
  collectionName: (id: number) => string | null;
  fieldName: (id: number) => string | null;
}) {
  const client = useApiClient();
  const qc = useQueryClient();
  const toast = useToast();
  const { can } = useArchive();
  const [confirm, setConfirm] = useState(false);
  const done = () => void qc.invalidateQueries({ queryKey: ["views"] });
  const fail = (title: string) => (e: Error) => toast({ tone: "red", title, body: e.message });
  const update = useMutation({
    mutationFn: (body: { state?: ViewState; shared?: boolean }) =>
      data(Views.updateView({ client, path: { vid: v.id }, body })),
    onSuccess: (r, body) => {
      done();
      toast({
        title:
          body.shared === undefined
            ? `“${r.name}” updated`
            : body.shared
              ? `“${r.name}” shared with ${r.namespace}`
              : `“${r.name}” is yours alone again`,
      });
    },
    onError: fail("Couldn’t change the view"),
  });
  const remove = useMutation({
    mutationFn: () => data(Views.deleteView({ client, path: { vid: v.id } })),
    onSuccess: () => {
      done();
      toast({ title: `“${v.name}” deleted` });
    },
    onError: fail("Couldn’t delete the view"),
  });
  const here = (v.namespace ?? null) === namespace;
  const noShare = v.namespace
    ? can("editor", v.namespace)
      ? null
      : needRole("editor", v.namespace)
    : "A view of every namespace can’t be shared";
  return (
    <li className="flex flex-col gap-1.5 border-t border-border py-2.5">
      <button
        type="button"
        onClick={onApply}
        className="flex min-w-0 flex-col gap-0.5 rounded-sm text-left hover:bg-surface focus-visible:outline-2 focus-visible:outline-blue"
      >
        <span className="flex min-w-0 items-center gap-1.5 text-[14px] font-semibold text-fg">
          {showing && <Check className="size-4 shrink-0 text-fg-accent" aria-label="Showing now" />}
          <span className="truncate">{v.name}</span>
          {v.shared && <Badge>Shared</Badge>}
        </span>
        <span className="text-[12px] text-fg-muted">{viewMeta(v)}</span>
        <span className="text-[12px] text-fg-secondary">
          {describeView(
            v.state,
            originName,
            (v.namespace ?? null) === namespace ? collectionName : undefined,
            (v.namespace ?? null) === namespace ? fieldName : undefined,
          )}
        </span>
      </button>
      {confirm ? (
        <div className="flex flex-wrap items-center gap-2" role="alert">
          <span className="flex-1 text-[12.5px] text-fg-strong">
            Delete “{v.name}”?{v.shared ? ` It goes for everyone in ${v.namespace}.` : ""}
          </span>
          <Button size="sm" variant="ghost" onClick={() => setConfirm(false)}>
            Keep it
          </Button>
          <Button size="sm" variant="danger" disabled={remove.isPending} onClick={() => remove.mutate()}>
            Delete
          </Button>
        </div>
      ) : (
        <div className="flex flex-wrap gap-1.5">
          {v.mine && (
            <Button
              size="sm"
              variant="ghost"
              disabled={showing || !here || update.isPending}
              disabledReason={
                showing
                  ? "It shows this already"
                  : !here
                    ? `Switch to ${v.namespace ?? "All namespaces"} to save what’s shown into it`
                    : undefined
              }
              onClick={() => update.mutate({ state: current })}
            >
              Save what’s shown
            </Button>
          )}
          {v.mine && (
            <Button
              size="sm"
              variant="ghost"
              disabled={(!v.shared && Boolean(noShare)) || update.isPending}
              disabledReason={!v.shared ? (noShare ?? undefined) : undefined}
              onClick={() => update.mutate({ shared: !v.shared })}
            >
              {v.shared ? "Stop sharing" : `Share with ${v.namespace ?? "a namespace"}`}
            </Button>
          )}
          <Button
            size="sm"
            variant="danger-ghost"
            disabled={!v.can_delete}
            disabledReason={`Only ${v.created_by ?? "its maker"} or an owner of ${v.namespace} can delete it`}
            onClick={() => setConfirm(true)}
          >
            Delete…
          </Button>
        </div>
      )}
    </li>
  );
}

/**
 * Saved views (L1): "Views" lists yours and the ones shared with your namespaces and brings one back (its namespace,
 * tab, filters and sort); "Save view" keeps what the Library shows under a name, for you or for its namespace.
 */
export function SavedViews({
  state,
  namespace,
  onApply,
  originName = (k) => k,
  collectionName = () => null,
  fieldName = () => null,
}: {
  state: ViewState;
  namespace: string | null;
  onApply: (v: SavedView) => void;
  /** What to call where recordings came from ("source:4" → the source's name). */
  originName?: (key: string) => string;
  /** What to call a collection of the namespace shown (null when it isn't one of its). */
  collectionName?: (id: number) => string | null;
  /** What to call a custom field of the namespace shown. */
  fieldName?: (id: number) => string | null;
}) {
  const views = useViews();
  const [listOpen, setListOpen] = useState(false);
  const [saveOpen, setSaveOpen] = useState(false);
  const all = views.data ?? [];
  const active = all.find((v) => isShowing(v, state, namespace)) ?? null;
  return (
    <>
      <Button
        variant="ghost"
        size="sm"
        icon={<Bookmark />}
        disabled={views.isSuccess && !all.length}
        disabledReason="No saved views yet: set up the Library, then Save view"
        onClick={() => setListOpen(true)}
      >
        <span className="max-w-[200px] truncate">
          Views{active && <span className="font-medium text-fg-secondary">: {active.name}</span>}
        </span>
        {all.length > 0 && <span className="font-mono text-[11px] font-medium text-fg-muted">{all.length}</span>}
      </Button>
      <Button variant="ghost" size="sm" icon={<BookmarkPlus />} onClick={() => setSaveOpen(true)}>
        Save view
      </Button>
      <SaveViewDialog
        open={saveOpen}
        onOpenChange={setSaveOpen}
        state={state}
        namespace={namespace}
        originName={originName}
        collectionName={collectionName}
        fieldName={fieldName}
      />
      <Dialog
        open={listOpen}
        onOpenChange={setListOpen}
        title="Saved views"
        description="Open one to show its namespace, tab, filters and sort."
      >
        {groupViews(all).map((g) => (
          <section key={g.title} className="flex flex-col" aria-label={g.title}>
            <h3 className="pb-1 text-[13px] font-bold text-fg">{g.title}</h3>
            <ul className="flex flex-col">
              {g.views.map((v) => (
                <ViewRow
                  key={v.id}
                  view={v}
                  showing={isShowing(v, state, namespace)}
                  current={state}
                  namespace={namespace}
                  originName={originName}
                  collectionName={collectionName}
                  fieldName={fieldName}
                  onApply={() => {
                    onApply(v);
                    setListOpen(false);
                  }}
                />
              ))}
            </ul>
          </section>
        ))}
      </Dialog>
    </>
  );
}
