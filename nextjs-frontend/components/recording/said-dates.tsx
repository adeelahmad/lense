"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import { CalendarClock, CalendarPlus } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { Dates } from "@/app/openapi-client";
import type { DateSaid } from "@/app/openapi-client/types.gen";
import { useApprovalsChanged } from "@/components/approvals/use-approvals";
import { usePlayerApi } from "@/components/player/media";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/panel";
import { useToast } from "@/components/ui/toast";
import { data, useApiClient } from "@/lib/api/browser";

const WEEKDAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const MONTH_NAMES = [
  "January",
  "February",
  "March",
  "April",
  "May",
  "June",
  "July",
  "August",
  "September",
  "October",
  "November",
  "December",
];

/** A date said, in words, the same in every locale: "Tue 20 Oct 2026", a span ("24 Oct – 25 Oct 2026"), a month
 * ("November 2026") or a year. */
export function dayWords(d: Pick<DateSaid, "day" | "until" | "period">): string {
  const [y, mo, dd] = d.day.split("-").map(Number);
  if (d.period === "year") return String(y);
  if (d.period === "month") return `${MONTH_NAMES[mo - 1]} ${y}`;
  const wd = WEEKDAYS[new Date(Date.UTC(y, mo - 1, dd)).getUTCDay()];
  if (d.until === d.day) return `${wd} ${dd} ${MONTHS[mo - 1]} ${y}`;
  const [y2, mo2, dd2] = d.until.split("-").map(Number);
  return `${dd} ${MONTHS[mo - 1]} – ${dd2} ${MONTHS[mo2 - 1]} ${y2}`;
}

/** Dates said in a recording (docs/processing.md#dates), each going to its moment, with Propose event when the
 * person can propose one (an admin, with a calendar connection that may add events): one click makes an event,
 * prefilled from the line, that waits in Approvals; nothing is added until they approve it there. */
export function SaidDates({ id }: { id: number }) {
  const client = useApiClient();
  const api = usePlayerApi();
  const toast = useToast();
  const changed = useApprovalsChanged();
  const [asked, setAsked] = useState<Record<string, number>>({});
  const q = useQuery({
    queryKey: ["recording", id, "dates"],
    queryFn: () => data(Dates.listDates({ client, query: { recording: id, limit: 200 } })),
    staleTime: 60_000,
  });
  const items = q.data?.items ?? [];
  const propose = useMutation({
    mutationFn: (d: DateSaid) =>
      data(Dates.proposeEvent({ client, body: { recording_id: id, segment: d.segment, index: indexIn(items, d) } })),
    onSuccess: (out, d) => {
      setAsked((a) => ({ ...a, [key(d)]: out.approval }));
      changed();
      toast({
        tone: "green",
        title: out.again ? "Already waiting in Approvals" : "Proposed. It waits in Approvals",
        body: "Nothing is added to the calendar until you approve it.",
      });
    },
    onError: (e: Error) => toast({ tone: "red", title: "Couldn’t propose it", body: e.message }),
  });
  if (!items.length) return null;
  return (
    <section className="flex flex-col gap-0.5" aria-label="Dates said">
      <Label as="h3" className="flex items-center gap-1.5 pb-1.5">
        <CalendarClock aria-hidden className="size-[13px]" />
        Dates said
      </Label>
      <ul className="m-0 flex list-none flex-col gap-0.5 p-0">
        {items.map((d) => {
          const waiting = asked[key(d)];
          return (
            <li
              key={key(d)}
              className="flex min-h-8 items-center gap-1 rounded-sm pl-2 pr-1 text-[13.5px] hover:bg-surface-neutral"
            >
              <button
                type="button"
                onClick={() => api.seek(d.t0 ?? 0, { manual: true })}
                className="flex min-w-0 flex-1 items-baseline gap-2.5 py-1 text-left"
                title={d.text ?? undefined}
              >
                <span className="shrink-0 font-medium text-fg">{dayWords(d)}</span>
                <span className="min-w-0 flex-1 truncate text-fg-secondary">“{d.said}”</span>
              </button>
              {q.data?.proposable &&
                (waiting ? (
                  <Link
                    href={`/approvals?id=${waiting}`}
                    className="shrink-0 px-2 text-[12.5px] text-fg-accent hover:underline"
                  >
                    Waiting in Approvals
                  </Link>
                ) : (
                  <Button
                    size="xs"
                    variant="ghost"
                    disabled={propose.isPending}
                    onClick={() => propose.mutate(d)}
                    aria-label={`Propose an event on ${dayWords(d)}`}
                    title="Propose an event from this line: it waits in Approvals for your yes"
                  >
                    <CalendarPlus aria-hidden />
                    Propose event
                  </Button>
                ))}
            </li>
          );
        })}
      </ul>
    </section>
  );
}

function cmp(a: string, b: string): number {
  return a < b ? -1 : a > b ? 1 : 0;
}

function key(d: DateSaid): string {
  return `${d.segment}:${d.day}:${d.said}`;
}

/** Which of its line's dates this is, as the API counts them: the earliest first. */
function indexIn(items: DateSaid[], d: DateSaid): number {
  return items
    .filter((x) => x.segment === d.segment)
    .sort((a, b) => cmp(a.day, b.day) || cmp(a.said, b.said))
    .findIndex((x) => key(x) === key(d));
}
