"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { Sources } from "@/app/openapi-client";
import type { Source } from "@/app/openapi-client/types.gen";
import { type RuleForm, ruleProblem, rulesFromWatch, rulesToApi } from "@/components/sources/route-model";
import { RoutingRules } from "@/components/sources/routing-rules";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { useToast } from "@/components/ui/toast";
import { data, useApiClient } from "@/lib/api/browser";
import { useArchive } from "@/lib/hooks/session";

/** An email account's shared routing rules, which every watch on it tries after its own. */
export function SharedRoutesDialog({
  source,
  open,
  onOpenChange,
}: {
  source: Source;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const client = useApiClient();
  const qc = useQueryClient();
  const toast = useToast();
  const { namespaces } = useArchive();
  const [rules, setRules] = useState<RuleForm[]>(() => rulesFromWatch(source.routes));
  const [tried, setTried] = useState(false);
  useEffect(() => {
    if (open) {
      setRules(rulesFromWatch(source.routes));
      setTried(false);
    }
  }, [open, source.routes]);

  const save = useMutation({
    mutationFn: () =>
      data(Sources.setSharedRoutes({ client, path: { sid: source.id }, body: { routes: rulesToApi(rules) } })),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["sources"] });
      toast({ title: "Routing rules saved", body: source.name });
      onOpenChange(false);
    },
    onError: (e: Error) => toast({ tone: "red", title: "Couldn’t save the routing rules", body: e.message }),
  });
  const submit = () => {
    setTried(true);
    if (!rules.some(ruleProblem)) save.mutate();
  };

  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      title={`Routing rules for ${source.name}`}
      className="max-w-[580px]"
      actions={
        <>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button variant="primary" onClick={submit} disabled={save.isPending}>
            {save.isPending ? "Saving…" : "Save"}
          </Button>
        </>
      }
    >
      <form
        onSubmit={(e) => {
          e.preventDefault();
          submit();
        }}
      >
        <RoutingRules
          shared
          rules={rules}
          onChange={setRules}
          namespaces={namespaces.map((n) => n.name)}
          home=""
          source={source.id}
          path=""
          showProblems={tried}
        />
      </form>
    </Dialog>
  );
}
