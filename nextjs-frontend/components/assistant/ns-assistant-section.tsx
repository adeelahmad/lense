"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Bot, Pin, PinOff, Plus } from "lucide-react";
import Link from "next/link";
import { useEffect, useState, type FormEvent } from "react";

import { Namespaces } from "@/app/openapi-client";
import type { AssistantMemory, NamespaceAssistantUpdate } from "@/app/openapi-client/types.gen";
import { isUnreachable } from "@/components/errors/error-states";
import { recordingHref } from "@/components/search/links";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Field, Input, Switch, Textarea } from "@/components/ui/field";
import { EmptyState, SkeletonRows } from "@/components/ui/states";
import { useToast } from "@/components/ui/toast";
import { data, useApiClient } from "@/lib/api/browser";
import { needRole } from "@/lib/hooks/session";

export const assistantKey = (ns: string) => ["namespace", ns, "assistant"] as const;
export const memoriesKey = (ns: string) => ["namespace", ns, "assistant", "memories"] as const;

/**
 * A namespace's own assistant (docs/assistant.md): owners turn it on, name it and give it instructions; it remembers
 * across conversations, and the memories are listed here to pin, correct or forget.
 */
export function NsAssistantSection({ ns, isOwner }: { ns: string; isOwner: boolean }) {
  const client = useApiClient();
  const qc = useQueryClient();
  const toast = useToast();
  const q = useQuery({
    queryKey: assistantKey(ns),
    queryFn: () => data(Namespaces.getNamespaceAssistant({ client, path: { name: ns } })),
  });
  const memories = useQuery({
    queryKey: memoriesKey(ns),
    queryFn: () => data(Namespaces.listAssistantMemories({ client, path: { name: ns } })),
  });
  const save = useMutation({
    mutationFn: (body: NamespaceAssistantUpdate) =>
      data(Namespaces.updateNamespaceAssistant({ client, path: { name: ns }, body })),
    onSuccess: (got, body) => {
      qc.setQueryData(assistantKey(ns), got);
      if (body.enabled !== undefined)
        toast({
          title: body.enabled ? `${got.name} is on` : `${got.name} is off`,
          body: body.enabled
            ? `Conversations about ${ns} now talk to it, and it remembers between them.`
            : `Conversations about ${ns} no longer read or add to its memory.`,
          tone: body.enabled ? "green" : undefined,
        });
      else toast({ title: "Assistant saved", tone: "green" });
    },
    onError: (e) => toast({ title: "Couldn’t save the assistant", body: (e as Error).message, tone: "red" }),
  });
  const refresh = () => {
    void qc.invalidateQueries({ queryKey: memoriesKey(ns) });
    void qc.invalidateQueries({ queryKey: assistantKey(ns), exact: true });
  };
  const add = useMutation({
    mutationFn: (text: string) =>
      data(Namespaces.addAssistantMemory({ client, path: { name: ns }, body: { text, pinned: false } })),
    onSuccess: refresh,
    onError: (e) => toast({ title: "Couldn’t add the memory", body: (e as Error).message, tone: "red" }),
  });
  const change = useMutation({
    mutationFn: ({ m, pinned }: { m: AssistantMemory; pinned: boolean }) =>
      data(Namespaces.updateAssistantMemory({ client, path: { name: ns, mid: m.id }, body: { pinned } })),
    onSuccess: refresh,
    onError: (e) => toast({ title: "Couldn’t change the memory", body: (e as Error).message, tone: "red" }),
  });
  const forget = useMutation({
    mutationFn: (m: AssistantMemory) =>
      data(Namespaces.forgetAssistantMemory({ client, path: { name: ns, mid: m.id } })),
    onSuccess: (_, m) => {
      refresh();
      toast({ title: "Forgotten", body: m.text });
    },
    onError: (e) => toast({ title: "Couldn’t forget it", body: (e as Error).message, tone: "red" }),
  });

  const a = q.data;
  const [name, setName] = useState("");
  const [instructions, setInstructions] = useState("");
  const [fact, setFact] = useState("");
  useEffect(() => {
    if (a) {
      setName(a.name);
      setInstructions(a.instructions ?? "");
    }
  }, [a]);
  const dirty = !!a && (name.trim() !== a.name || instructions.trim() !== (a.instructions ?? ""));

  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (dirty) save.mutate({ name: name.trim(), instructions: instructions.trim() });
  };
  const remember = (e: FormEvent) => {
    e.preventDefault();
    const text = fact.trim();
    if (text) add.mutate(text, { onSuccess: () => setFact("") });
  };

  return (
    <section
      id="assistant"
      aria-labelledby="assistant-heading"
      className="flex scroll-mt-4 flex-col gap-3.5 rounded-md border border-border bg-background px-4 py-5 sm:px-6"
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex min-w-0 flex-1 basis-[260px] flex-col gap-1">
          <h2 id="assistant-heading" className="text-[17px] font-bold leading-tight text-fg">
            Assistant
          </h2>
          <p className="text-[13px] leading-[1.45] text-fg-secondary">
            {ns}’s own assistant answers every conversation about {ns} alone. It remembers decisions, preferences and
            facts between conversations, each linked to where it came from, and asks before changing anything.
          </p>
        </div>
        {a && (
          <Switch
            checked={a.enabled}
            disabled={!isOwner || save.isPending}
            onCheckedChange={(on) => save.mutate({ enabled: on })}
            label={a.enabled ? "On" : "Off"}
            aria-label={`${ns}’s assistant`}
          />
        )}
      </div>
      {q.isPending ? (
        <SkeletonRows rows={2} />
      ) : q.isError ? (
        <EmptyState
          tone="error"
          icon={<Bot />}
          title={isUnreachable(q.error) ? "Can’t reach the server" : "Couldn’t load the assistant"}
          actions={<Button onClick={() => q.refetch()}>Try again</Button>}
        >
          {q.error.message}
        </EmptyState>
      ) : (
        <>
          <form onSubmit={submit} className="flex flex-col gap-3">
            <Field label="Name" hint={!isOwner ? needRole("owner", ns) : undefined}>
              {(f) => (
                <Input
                  id={f.id}
                  aria-describedby={f.describedBy}
                  value={name}
                  maxLength={60}
                  disabled={!isOwner}
                  onChange={(e) => setName(e.target.value)}
                />
              )}
            </Field>
            <Field label="Instructions" optional hint="Read with every question, e.g. who it helps and how to answer.">
              {(f) => (
                <Textarea
                  id={f.id}
                  aria-describedby={f.describedBy}
                  value={instructions}
                  maxLength={4000}
                  rows={3}
                  disabled={!isOwner}
                  onChange={(e) => setInstructions(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) submit(e);
                  }}
                />
              )}
            </Field>
            {isOwner && (
              <div className="flex gap-2">
                <Button type="submit" size="sm" variant="primary" disabled={!dirty || save.isPending}>
                  Save
                </Button>
                {a?.enabled && (
                  <Link
                    className="self-center text-[13px] font-semibold text-fg-accent hover:underline"
                    href={`/chat?ns=${encodeURIComponent(ns)}`}
                  >
                    Talk to {a.name} →
                  </Link>
                )}
              </div>
            )}
          </form>

          <div className="flex flex-col gap-2">
            <span className="text-[13px] font-bold text-fg-strong">
              What it remembers{a?.memories ? ` (${a.memories})` : ""}
            </span>
            <form onSubmit={remember} className="flex gap-2">
              <Input
                aria-label="Something to remember"
                placeholder="Tell it something to remember"
                value={fact}
                maxLength={500}
                onChange={(e) => setFact(e.target.value)}
              />
              <Button type="submit" size="sm" icon={<Plus />} disabled={!fact.trim() || add.isPending}>
                Add
              </Button>
            </form>
            {memories.isPending ? (
              <SkeletonRows rows={2} />
            ) : !memories.data?.length ? (
              <p className="text-[13px] text-fg-muted">
                Nothing yet. It keeps what it learns in conversations{a?.enabled ? "" : " once it’s on"}.
              </p>
            ) : (
              <ul className="flex flex-col">
                {memories.data.map((m) => (
                  <li key={m.id} className="flex items-start gap-3 border-t border-border py-2.5 first:border-t-0">
                    <div className="flex min-w-0 flex-1 flex-col gap-0.5">
                      <span className="text-[14px] text-fg">{m.text}</span>
                      <span className="text-[12.5px] text-fg-muted">
                        {m.pinned && <Badge className="mr-1.5">Pinned</Badge>}
                        {m.recording != null ? (
                          <Link className="text-fg-accent hover:underline" href={recordingHref(m.recording, m.t0)}>
                            {m.title || "A recording"}
                            {m.time ? ` at ${m.time}` : ""}
                          </Link>
                        ) : m.author === "person" ? (
                          "Written here"
                        ) : (
                          "Told in a conversation"
                        )}
                        {m.created_at ? ` · ${m.created_at.slice(0, 10)}` : ""}
                      </span>
                    </div>
                    <Button
                      variant="ghost"
                      size="xs"
                      icon={m.pinned ? <PinOff /> : <Pin />}
                      aria-label={m.pinned ? "Unpin" : "Pin: read with every question"}
                      disabled={change.isPending}
                      onClick={() => change.mutate({ m, pinned: !m.pinned })}
                    />
                    <Button
                      variant="danger-ghost"
                      size="xs"
                      disabled={forget.isPending}
                      onClick={() => forget.mutate(m)}
                      aria-label={`Forget: ${m.text}`}
                    >
                      Forget
                    </Button>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </>
      )}
    </section>
  );
}
