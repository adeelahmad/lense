"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { ContentTypes } from "@/app/openapi-client";
import { useContentTypes } from "@/components/pipelines/catalog-header";
import { Select } from "@/components/ui/field";
import { useToast } from "@/components/ui/toast";
import { data, useApiClient } from "@/lib/api/browser";

/** The resource's content type: chosen, or recognised from its file. Editors can choose another of its base type. */
export function ContentTypePicker({ rid, editable }: { rid: number; editable: boolean }) {
  const client = useApiClient();
  const qc = useQueryClient();
  const toast = useToast();
  const types = useContentTypes();
  const cur = useQuery({
    queryKey: ["recording-content-type", rid],
    queryFn: () => data(ContentTypes.getRecordingContentType({ client, path: { rid } })),
  });
  const set = useMutation({
    mutationFn: (key: string | null) =>
      data(ContentTypes.setRecordingContentType({ client, path: { rid }, body: { content_type: key } })),
    onSuccess: (r) => {
      qc.setQueryData(["recording-content-type", rid], r);
      toast({ tone: "green", title: `Now a ${r.content_type.label}`, body: "New runs use its pipeline." });
    },
    onError: (e: Error) => toast({ tone: "red", title: "Couldn’t change it", body: e.message }),
  });
  if (!cur.data) return null;
  const t = cur.data.content_type;
  if (!editable) return <>{`${t.label}${cur.data.chosen ? "" : " (recognised)"}`}</>;
  const same = (types.data?.types ?? []).filter((x) => x.base === t.base);
  return (
    <Select
      aria-label="Content type"
      size="sm"
      className="w-[220px]"
      value={cur.data.chosen ? t.key : ""}
      disabled={set.isPending}
      onChange={(e) => set.mutate(e.target.value || null)}
      options={[
        { value: "", label: `Recognised: ${cur.data.chosen ? "from the file" : t.label}` },
        ...same.map((x) => ({ value: x.key, label: x.label })),
      ]}
    />
  );
}
