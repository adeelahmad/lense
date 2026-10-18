import type { Schedule } from "@/app/openapi-client/types.gen";
import {
  changesOf,
  counts,
  draftOf,
  filtered,
  lastOf,
  noNext,
  presetsFor,
  whatText,
} from "@/components/schedules/model";

function row(over: Partial<Schedule> = {}): Schedule {
  return {
    id: "routine:3",
    kind: "routine",
    name: "Sync nightly",
    on: "schedule",
    cron: "0 3 * * *",
    timezone: "UTC",
    text: "every day at 03:00",
    enabled: true,
    state: "on",
    next_run_at: "2026-10-18T03:00:00+00:00",
    href: "/routines/3",
    namespace_names: null,
    ...over,
  };
}

const morning = row({
  id: "automation:4:morning",
  kind: "automation",
  name: "Mornings",
  trigger: "morning",
  cron: "0 7 * * 1-5",
  timezone: "Europe/London",
  text: "every weekday at 07:00",
  state: "waiting",
  href: "/automations/4",
  namespace_names: ["pods"],
});
const once = row({
  id: "automation:4:once",
  kind: "automation",
  name: "Mornings",
  trigger: "once",
  on: "at",
  cron: null,
  at: "2026-11-02T07:30:00",
  text: "once, on Mon 2 Nov at 07:30",
  state: "waiting",
  namespace_names: ["pods"],
});
const backup = row({
  id: "backups",
  kind: "backups",
  name: "Back up the database",
  on: "manual",
  cron: null,
  enabled: false,
});

test("filters by kind and counts each, keeping the server's order", () => {
  const rows = [morning, row(), backup, once];
  expect(counts(rows)).toEqual({ all: 4, routine: 1, automation: 2, backups: 1 });
  expect(filtered(rows, "automation").map((r) => r.id)).toEqual(["automation:4:morning", "automation:4:once"]);
  expect(filtered(rows, "all")).toBe(rows);
});

test("says what each row is, with its trigger and namespaces", () => {
  expect(whatText(row())).toBe("Routine · every namespace");
  expect(whatText(morning)).toBe("Automation · morning · pods");
  expect(whatText(backup)).toBe("Backup");
});

test("labels last runs and why there's no next one", () => {
  expect(lastOf("done")).toEqual({ label: "Done", tone: "green" });
  expect(lastOf("error").tone).toBe("red");
  expect(lastOf(null).label).toBe("—");
  expect(noNext(row({ enabled: false }))).toBeNull();
  expect(noNext(row({ on: "manual", cron: null }))).toBe("Only by hand");
  expect(noNext(row({ ...backup, enabled: true }))).toBe("No schedule");
  expect(noNext({ ...once, state: "done" })).toBe("Its time has passed");
  expect(noNext({ ...once, state: "missed" })).toBe("Missed while Lens was off");
  expect(noNext(row({ running: true }))).toBe("Running now");
});

test("offers by hand to routines, no schedule to the backup, and neither to automations", () => {
  expect(presetsFor(row()).map((p) => p.value)).toContain("manual");
  expect(presetsFor(morning).map((p) => p.value)).not.toContain("manual");
  expect(presetsFor(backup).find((p) => p.value === "manual")?.label).toBe("No scheduled backups");
});

test("sends only what changed: a cron, by hand, an at trigger's time, the time zone", () => {
  const d = draftOf(row());
  expect(d).toEqual({ preset: "daily", custom: "0 3 * * *", at: "", timezone: "UTC" });
  expect(changesOf(row(), d)).toBeNull();
  expect(changesOf(row(), { ...d, preset: "weekdays" })).toEqual({ cron: "0 9 * * 1-5" });
  expect(changesOf(row(), { ...d, preset: "manual" })).toEqual({ cron: null });
  expect(changesOf(row(), { ...d, preset: "custom", custom: " 30  2 * * 1-5 " })).toEqual({ cron: "30 2 * * 1-5" });
  expect(changesOf(morning, { ...draftOf(morning), timezone: "UTC" })).toEqual({ timezone: "UTC" });
  expect(draftOf(once).at).toBe("2026-11-02T07:30");
  expect(changesOf(once, { ...draftOf(once), at: "2026-11-03T08:00" })).toEqual({ at: "2026-11-03T08:00" });
});
