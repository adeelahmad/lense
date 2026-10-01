import { useCallback } from "react";

import { useRec } from "@/components/recording/context";
import { useNamespaceFaces } from "@/components/recording/hooks";

/** Face colours follow the linked speaker where there is one (same person, same colour), else the order people appear. */
export function useFaceColors() {
  const { model, ns } = useRec();
  const faces = useNamespaceFaces(ns, model.facesMode === "recognize");
  return useCallback(
    (i: number) => {
      const fid = model.faces[i]?.face;
      const linked = fid
        ? (
            (faces.data?.faces ?? []) as {
              id: number;
              speaker?: number | null;
            }[]
          ).find((f) => f.id === fid)?.speaker
        : null;
      const spk = linked != null ? model.speakers.find((s) => s.id === linked) : null;
      return spk ? spk.color : `var(--spk-${((model.speakers.length + i) % 8) + 1})`;
    },
    [model.faces, model.speakers, faces.data],
  );
}
