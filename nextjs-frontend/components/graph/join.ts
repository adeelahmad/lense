"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useCallback } from "react";

import { Speakers } from "@/app/openapi-client";
import { useLinkEntities, useMergeEntities } from "@/components/entities/curate";
import type { GraphNode } from "@/components/graph/model";
import { useToast } from "@/components/ui/toast";
import { ApiError, data, useApiClient } from "@/lib/api/browser";
import { useArchive } from "@/lib/hooks/session";

/** What one node can join: an entity of one namespace joins another entity (merged in its namespace, linked across
 * namespaces) and a speaker joins a speaker of its namespace. A global node joining several namespaces joins nothing. */
export function joinKind(n: Pick<GraphNode, "kind" | "refs" | "ns"> | null | undefined): "entity" | "speaker" | null {
  if (!n || n.refs.length !== 1 || n.ns.length !== 1) return null;
  return n.kind === "entity" || n.kind === "speaker" ? n.kind : null;
}

/** How `from` joins `into` (`into` is kept), or why it can't. */
export function joinPlan(
  from: Pick<GraphNode, "id" | "kind" | "refs" | "ns">,
  into: Pick<GraphNode, "id" | "kind" | "refs" | "ns">,
): "merge" | "link" | "merge-speaker" | string {
  const k = joinKind(from);
  if (into.id === from.id) return "Pick another node to merge into.";
  if (!k || joinKind(into) !== k)
    return k === "speaker" ? "A speaker merges into another speaker." : "An entity merges into another entity.";
  if (k === "speaker") return from.ns[0] === into.ns[0] ? "merge-speaker" : "Speakers are merged within one namespace.";
  return from.ns[0] === into.ns[0] ? "merge" : "link";
}

/** Merging nodes on the graph canvas: entities (with Undo), entities of two namespaces linked, and speakers. */
export function useGraphJoin() {
  const client = useApiClient();
  const qc = useQueryClient();
  const toast = useToast();
  const { can } = useArchive();
  const { merge } = useMergeEntities();
  const link = useLinkEntities();
  const joinable = useCallback(
    (n: GraphNode | null | undefined): boolean => Boolean(n && joinKind(n) && can("editor", n.ns[0])),
    [can],
  );
  const fail = useCallback(
    (title: string, err: unknown) =>
      toast({ title, body: err instanceof ApiError ? err.message : "Please try again.", tone: "red" }),
    [toast],
  );
  const speakers = useCallback(
    async (from: GraphNode, into: GraphNode): Promise<boolean> => {
      const refresh = () => {
        for (const k of ["graph", "speakers", "speaker"]) void qc.invalidateQueries({ queryKey: [k] });
      };
      try {
        const r = await data(
          Speakers.mergeSpeaker({ client, path: { sid: from.refs[0] }, body: { into: into.refs[0] } }),
        );
        refresh();
        toast({
          title: `Merged ${from.label} into ${into.label}`,
          tone: "green",
          duration: 10000,
          action: {
            label: "Undo",
            onClick: () =>
              void data(Speakers.undoSpeakerMerge({ client, path: { mid: r.merge_id } }))
                .then(() => {
                  refresh();
                  toast({ title: "Merge undone", tone: "green" });
                })
                .catch((err) => fail("Couldn’t undo the merge", err)),
          },
        });
        return true;
      } catch (err) {
        fail("Couldn’t merge them", err);
        return false;
      }
    },
    [client, qc, toast, fail],
  );
  /** Join `from` into `into`; true when `from` is gone from the graph (merged). */
  const join = useCallback(
    async (from: GraphNode, into: GraphNode): Promise<boolean> => {
      const plan = joinPlan(from, into);
      if (plan === "merge-speaker" || plan === "merge" || plan === "link") {
        if (!joinable(into)) {
          toast({
            title: "You can’t change that one",
            body: `It needs the editor role in ${into.ns[0]}.`,
            tone: "red",
          });
          return false;
        }
      }
      const a = { id: from.refs[0], name: from.label };
      const b = { id: into.refs[0], name: into.label };
      if (plan === "merge") return merge(b, [a]);
      if (plan === "merge-speaker") return speakers(from, into);
      if (plan === "link") {
        await link(b, a);
        return false;
      }
      toast({ title: "Those can’t be merged", body: plan, tone: "red" });
      return false;
    },
    [joinable, merge, speakers, link, toast],
  );
  return { joinable, join };
}
