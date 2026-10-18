"use client";

import { useQueryClient } from "@tanstack/react-query";
import { CalendarClock, Plus, Trash2 } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";

import { Projects } from "@/app/openapi-client";
import type { Project, ProjectSchedule } from "@/app/openapi-client/types.gen";
import { Button, IconButton } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { Field, Input, Select, Switch, Textarea } from "@/components/ui/field";
import { DateTime } from "@/components/ui/states";
import { useToast } from "@/components/ui/toast";
import { data, useApiClient } from "@/lib/api/browser";

/** The schedules most people want, as cron; "custom" takes one typed in. */
export const WHEN = [
  { value: "0 8 * * *", label: "Every day at 8:00" },
  { value: "0 9 * * 1-5", label: "Every weekday at 9:00" },
  { value: "0 9 * * 1", label: "Every Monday at 9:00" },
  { value: "0 18 * * 5", label: "Every Friday at 18:00" },
  { value: "0 9 1 * *", label: "The 1st of every month at 9:00" },
  { value: "0 * * * *", label: "Every hour" },
  { value: "custom", label: "Custom (cron)" },
] as const;

/** The browser's time zone, so "8:00" is the person's 8:00. */
export function localZone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  } catch {
    return "UTC";
  }
}

function AddScheduleDialog({
  project,
  open,
  onOpenChange,
}: {
  project: Project;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const client = useApiClient();
  const qc = useQueryClient();
  const [prompt, setPrompt] = useState("");
  const [when, setWhen] = useState<string>(WHEN[0].value);
  const [cron, setCron] = useState("");
  const [thread, setThread] = useState<"new" | "same">("new");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const zone = localZone();

  useEffect(() => {
    if (open) {
      setPrompt("");
      setWhen(WHEN[0].value);
      setCron("");
      setThread("new");
      setError(null);
    }
  }, [open]);

  const add = async () => {
    setBusy(true);
    setError(null);
    try {
      await data(
        Projects.addSchedule({
          client,
          path: { pid: project.id },
          body: { prompt: prompt.trim(), cron: when === "custom" ? cron.trim() : when, timezone: zone, thread },
        }),
      );
      void qc.invalidateQueries({ queryKey: ["project", project.id] });
      onOpenChange(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn’t add the schedule.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      title="Ask on a schedule"
      description="The assistant answers it in the background each time, with your tools and approvals."
      actions={
        <>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button
            variant="primary"
            disabled={busy || !prompt.trim() || (when === "custom" && !cron.trim())}
            onClick={add}
          >
            {busy ? "Adding…" : "Add schedule"}
          </Button>
        </>
      }
    >
      <Field label="What to ask">
        {(f) => (
          <Textarea
            id={f.id}
            rows={3}
            value={prompt}
            maxLength={4000}
            placeholder="e.g. What did we get done this week, and what's next?"
            onChange={(e) => setPrompt(e.target.value)}
          />
        )}
      </Field>
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="When" hint={`In your time zone, ${zone}.`}>
          {(f) => (
            <Select
              id={f.id}
              aria-describedby={f.describedBy}
              value={when}
              onChange={(e) => setWhen(e.target.value)}
              options={WHEN.map((w) => ({ value: w.value, label: w.label }))}
            />
          )}
        </Field>
        <Field label="Where">
          {(f) => (
            <Select
              id={f.id}
              value={thread}
              onChange={(e) => setThread(e.target.value as "new" | "same")}
              options={[
                { value: "new", label: "A new thread each time" },
                { value: "same", label: "The same thread each time" },
              ]}
            />
          )}
        </Field>
      </div>
      {when === "custom" && (
        <Field label="Cron" hint="Minute, hour, day of month, month, day of week: 30 7 * * 1-5 is 7:30 on weekdays.">
          {(f) => (
            <Input
              id={f.id}
              aria-describedby={f.describedBy}
              mono
              value={cron}
              maxLength={200}
              placeholder="30 7 * * 1-5"
              onChange={(e) => setCron(e.target.value)}
            />
          )}
        </Field>
      )}
      {error && (
        <p role="alert" className="m-0 text-[13px] text-red-dark">
          {error}
        </p>
      )}
    </Dialog>
  );
}

function ScheduleRow({ project, schedule }: { project: Project; schedule: ProjectSchedule }) {
  const client = useApiClient();
  const qc = useQueryClient();
  const toast = useToast();
  const refresh = () => void qc.invalidateQueries({ queryKey: ["project", project.id] });
  const fail = (e: unknown) =>
    toast({ title: "Couldn’t change the schedule", body: e instanceof Error ? e.message : undefined, tone: "red" });

  const toggle = async (on: boolean) => {
    try {
      await data(Projects.updateSchedule({ client, path: { pid: project.id, sid: schedule.id }, body: { on } }));
    } catch (e) {
      fail(e);
    }
    refresh();
  };
  const remove = async () => {
    try {
      await data(Projects.removeSchedule({ client, path: { pid: project.id, sid: schedule.id } }));
    } catch (e) {
      return fail(e);
    }
    refresh();
    const { prompt, cron, timezone, thread } = schedule;
    toast({
      title: "Schedule removed",
      tone: "intent",
      action: {
        label: "Undo",
        onClick: () =>
          void data(
            Projects.addSchedule({
              client,
              path: { pid: project.id },
              body: { prompt, cron, timezone, thread: thread === "same" ? "same" : "new" },
            }),
          )
            .then(refresh)
            .catch(fail),
      },
    });
  };

  const on = schedule.on ?? true;
  const words = schedule.when || schedule.cron;
  return (
    <li className="flex items-start gap-3 px-4 py-3">
      <Switch
        className="mt-0.5"
        aria-label={on ? "Switch off" : "Switch on"}
        checked={on}
        onCheckedChange={(v) => void toggle(v)}
      />
      <div className="flex min-w-0 flex-1 flex-col gap-0.5">
        <span className={on ? "line-clamp-2 text-fg" : "line-clamp-2 text-fg-muted"}>{schedule.prompt}</span>
        <span className="text-[13px] text-fg-secondary">
          {words.charAt(0).toUpperCase() + words.slice(1)}
          {schedule.timezone && schedule.timezone !== "UTC" ? ` (${schedule.timezone})` : " (UTC)"},{" "}
          {schedule.thread === "same" ? (
            schedule.chat != null ? (
              <Link href={`/chat/${schedule.chat}`} className="hover:underline">
                in its thread
              </Link>
            ) : (
              "in one thread"
            )
          ) : (
            "in a new thread each time"
          )}
          {on && schedule.next_at ? (
            <>
              {" "}
              · next <DateTime iso={schedule.next_at} />
            </>
          ) : !on ? (
            " · off"
          ) : null}
        </span>
        {schedule.last && !schedule.last.ok && (
          <span role="status" className="text-[13px] text-red-dark">
            Last time it didn’t ask: {schedule.last.error}
          </span>
        )}
      </div>
      <IconButton label="Remove schedule" onClick={() => void remove()}>
        <Trash2 />
      </IconButton>
    </li>
  );
}

/** A project's schedules: prompts asked on a schedule, each switched on or off in one click, removed with Undo. */
export function Schedules({ project }: { project: Project }) {
  const [adding, setAdding] = useState(false);
  const list = project.schedules ?? [];
  return (
    <section aria-labelledby="project-schedules" className="flex flex-col gap-2">
      <div className="flex items-center gap-2">
        <h2 id="project-schedules" className="m-0 flex-1 text-[15px] font-bold text-fg">
          Schedules
        </h2>
        <Button variant="ghost" size="sm" icon={<Plus />} onClick={() => setAdding(true)}>
          Ask on a schedule
        </Button>
      </div>
      {list.length ? (
        <ul className="m-0 list-none divide-y divide-border rounded-md border border-border p-0">
          {list.map((s) => (
            <ScheduleRow key={s.id} project={project} schedule={s} />
          ))}
        </ul>
      ) : (
        <p className="m-0 flex items-center gap-2 text-[14px] text-fg-secondary">
          <CalendarClock aria-hidden className="size-4" />
          Nothing scheduled. A weekly check-in or a daily summary keeps the project moving.
        </p>
      )}
      <AddScheduleDialog project={project} open={adding} onOpenChange={setAdding} />
    </section>
  );
}
