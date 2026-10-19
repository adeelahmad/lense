"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ScanFace } from "lucide-react";
import Link from "next/link";
import { useEffect, useRef, useState, type FormEvent } from "react";

import { Video } from "@/app/openapi-client";
import type { FaceNamed, UnnamedFace } from "@/app/openapi-client/types.gen";
import { useBlur } from "@/components/nsfw/nsfw";
import { screenTime } from "@/components/recording/video/model";
import { Banner } from "@/components/ui/banner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/field";
import { EmptyState, SkeletonRows } from "@/components/ui/states";
import { useToast } from "@/components/ui/toast";
import { ApiError, data, useApiClient } from "@/lib/api/browser";
import { plural } from "@/lib/format";
import { needRole, useArchive } from "@/lib/hooks/session";
import { cn } from "@/lib/utils";

/** The namespace's faces nobody has named yet, the largest group first (only for people with a role in it). Under
 * ["faces", ns], so whatever changes the namespace's faces refreshes it too. */
export function useUnnamedFaces(ns: string | null | undefined) {
  const client = useApiClient();
  const { can } = useArchive();
  return useQuery({
    queryKey: ["faces", ns, "unnamed"],
    queryFn: () => data(Video.getUnnamedFaces({ client, path: { name: ns as string }, query: { limit: 500 } })),
    enabled: Boolean(ns) && can("viewer", ns),
    staleTime: 30_000,
  });
}

/**
 * Name the namespace's unnamed faces one after another: the first one's name field has focus (with the name Lens
 * would offer, selected so typing replaces it), Enter names it and moves on to the next, and the notice that follows
 * has Undo. A name another face has joins that person (a merge); any other name becomes the face's own.
 */
export function NameFaces({ ns }: { ns: string }) {
  const client = useApiClient();
  const qc = useQueryClient();
  const toast = useToast();
  const { can } = useArchive();
  const q = useUnnamedFaces(ns);
  const canName = can("editor", ns);
  const [named, setNamed] = useState<Set<number>>(new Set()); // named here, until the list comes back without them
  const [active, setActive] = useState<number | null>(null);
  const groups = (q.data?.groups ?? []).filter((g) => !named.has(g.id));
  const current = active != null && groups.some((g) => g.id === active) ? active : (groups[0]?.id ?? null);
  const refresh = () => qc.invalidateQueries({ queryKey: ["faces", ns] });
  const fail = (title: string) => (e: unknown) =>
    toast({ title, body: e instanceof ApiError ? e.message : "Please try again.", tone: "red" });
  const show = (fid: number, on: boolean) =>
    setNamed((s) => {
      const next = new Set(s);
      if (on) next.delete(fid);
      else next.add(fid);
      return next;
    });

  const undo = useMutation({
    mutationFn: ({ g, r }: { g: UnnamedFace; r: FaceNamed }) =>
      r.merge != null
        ? data(Video.undoFaceMerge({ client, path: { mid: r.merge } }))
        : data(Video.renameFace({ client, path: { fid: g.id }, body: { name: "" } })),
    onSuccess: (_r, { g }) => {
      show(g.id, true);
      setActive(g.id);
      void refresh();
    },
    onError: fail("Couldn’t undo it"),
  });
  const name = useMutation({
    mutationFn: ({ g, value }: { g: UnnamedFace; value: string; next: number | null }) =>
      data(Video.nameFace({ client, path: { fid: g.id }, body: { name: value } })),
    onSuccess: (r, { g, next }) => {
      show(g.id, false);
      setActive(next);
      void refresh();
      toast({
        title: r.merge != null ? `${g.label} joined ${r.name}` : `${g.label} is ${r.name}`,
        tone: "green",
        action: { label: "Undo", onClick: () => undo.mutate({ g, r }) },
      });
    },
    onError: fail("Couldn’t name it"),
  });

  if (q.isLoading) return <SkeletonRows rows={4} />;
  if (q.isError)
    return (
      <Banner
        tone="error"
        title="Couldn’t load the faces."
        action={
          <Button size="sm" variant="secondary" onClick={() => q.refetch()}>
            Try again
          </Button>
        }
      >
        {q.error.message}
      </Banner>
    );
  if (q.data?.mode !== "recognize")
    return (
      <EmptyState
        icon={<ScanFace />}
        title={`Faces aren’t recognised in ${ns}`}
        className="rounded-lg border border-border"
      >
        Faces are grouped across a namespace, to be named, only where an owner has turned face recognition on (in a
        video’s People tab). Nothing changes until they do.
      </EmptyState>
    );
  if (!groups.length)
    return (
      <EmptyState icon={<ScanFace />} title="Every face has a name" className="rounded-lg border border-border">
        Faces found from now on that aren’t anyone {ns} knows wait here for a name.
      </EmptyState>
    );
  return (
    <div className="flex flex-col gap-3">
      <p className="m-0 text-[13px] leading-snug text-fg-secondary">
        {plural(groups.length, "face")} without a name, the most seen first. Type who it is and press Enter for the
        next; a name someone already has joins that person.
      </p>
      <ul className="m-0 flex list-none flex-col gap-2 p-0" aria-label={`Unnamed faces in ${ns}`}>
        {groups.map((g, i) => (
          <FaceGroup
            key={g.id}
            g={g}
            active={g.id === current}
            disabled={canName ? undefined : needRole("editor", ns)}
            pending={name.isPending && name.variables?.g.id === g.id}
            onFocus={() => setActive(g.id)}
            onName={(value) => name.mutate({ g, value, next: (groups[i + 1] ?? groups[i - 1])?.id ?? null })}
          />
        ))}
      </ul>
    </div>
  );
}

function FaceGroup({
  g,
  active,
  disabled,
  pending,
  onFocus,
  onName,
}: {
  g: UnnamedFace;
  active: boolean;
  /** Why this person can't name faces, when they can't. */
  disabled?: string;
  pending: boolean;
  onFocus: () => void;
  onName: (name: string) => void;
}) {
  const blur = useBlur();
  const [value, setValue] = useState(g.guess ?? "");
  const input = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (active && !disabled) input.current?.focus();
  }, [active, disabled]);
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (value.trim()) onName(value.trim());
  };
  const seen = [
    plural(g.recordings.length, "asset"),
    g.screen_ms ? `${screenTime(g.screen_ms)} on screen` : null,
    g.pages ? plural(g.pages, "page") : null,
  ]
    .filter(Boolean)
    .join(" · ");
  return (
    <li
      aria-label={g.label}
      className={cn(
        "flex flex-wrap items-center gap-3 rounded-lg border p-3",
        active ? "border-blue-border bg-blue-surface" : "border-border",
      )}
    >
      <div className="flex shrink-0 gap-1.5">
        {g.thumbnails.map((t, k) => (
          // eslint-disable-next-line @next/next/no-img-element
          <img
            key={k}
            src={t.cover_url}
            alt=""
            loading="lazy"
            className={cn("size-14 rounded-[10px] border border-border bg-surface-neutral object-cover", blur)}
          />
        ))}
      </div>
      <div className="min-w-[180px] flex-1 basis-[200px]">
        <div className="text-[14px] font-bold leading-tight text-fg">{g.label}</div>
        <div className="text-[12.5px] leading-snug text-fg-secondary">{seen}</div>
        <div className="mt-0.5 truncate text-[12.5px] leading-snug">
          {g.recordings.slice(0, 3).map((r, k) => (
            <span key={r.id}>
              {k > 0 && <span className="text-fg-muted"> · </span>}
              <Link href={`/resources/${r.id}`} className="font-medium text-fg hover:text-fg-accent hover:underline">
                {r.title || `Asset ${r.id}`}
              </Link>
            </span>
          ))}
          {g.recordings.length > 3 && <span className="text-fg-muted"> · {g.recordings.length - 3} more</span>}
        </div>
      </div>
      <form onSubmit={submit} className="flex w-full items-center gap-2 sm:w-auto">
        <Input
          ref={input}
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onFocus={(e) => {
            e.target.select();
            onFocus();
          }}
          maxLength={80}
          placeholder="Who is it?"
          aria-label={`Name for ${g.label}`}
          disabled={Boolean(disabled) || pending}
          className="h-9 min-w-0 flex-1 text-[13.5px] sm:w-[220px]"
        />
        <Button
          type="submit"
          size="sm"
          variant="primary"
          disabled={Boolean(disabled) || pending || !value.trim()}
          disabledReason={disabled}
        >
          Name
        </Button>
      </form>
    </li>
  );
}
