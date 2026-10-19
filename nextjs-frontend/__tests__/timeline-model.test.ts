import {
  automationWhere,
  familyOf,
  filterCount,
  fromParams,
  group,
  groupSummary,
  isFiltered,
  kindsByFamily,
  mergeNew,
  newest,
  NO_FILTERS,
  objectFields,
  objectHref,
  snoozeUntil,
  localInput,
  tomorrowMorning,
  toParams,
  toQuery,
  unseen,
  type Activity,
  fromViewState,
  toViewState,
  sameFilters,
  viewName,
} from "@/components/timeline/model";

const MIN = 60_000;
const T0 = Date.parse("2026-10-15T12:00:00.000Z");

function act(
  id: string,
  minutesAgo: number,
  kind = "asset.added",
  actor = "lens:source/1",
  name = "Work Gmail",
): Activity {
  return {
    id,
    type: "Create",
    kind,
    published: new Date(T0 - minutesAgo * MIN).toISOString(),
    actor: { type: "Service", id: actor, name },
    object: { type: "Note", id: `lens:recording/${id}`, name: id, url: `/resources/${id}` },
    summary: `${name} added “${id}”`,
    space: 3,
    tag: [],
    lens: { source: "recordings", pos: [] },
  };
}

describe("filters in the address", () => {
  it("go to and from the query, so a view can be linked", () => {
    const f = {
      ...NO_FILTERS,
      kinds: ["email.received", "file.added"],
      actor: ["lens:source/12"],
      namespace: ["family"],
      tag: "St Mary's School",
      from: "2026-10-01",
      to: "2026-10-15",
      automation: "4",
      q: "school trip",
      unreviewed: true,
      important: true,
      order: "for_you" as const,
    };
    const p = toParams(f);
    expect(p.toString()).toBe(
      "kinds=email.received%2Cfile.added&actor=lens%3Asource%2F12&ns=family&tag=St+Mary%27s+School&from=2026-10-01&to=2026-10-15&automation=4&q=school+trip&unreviewed=1&important=1&order=for_you",
    );
    expect(fromParams(p)).toEqual(f);
    expect(filterCount(f)).toBe(10);
  });

  it("keep the For you order as a view, not a filter", () => {
    const f = { ...NO_FILTERS, order: "for_you" as const };
    expect(toParams(f).toString()).toBe("order=for_you");
    expect(isFiltered(f)).toBe(false);
    expect(filterCount(f)).toBe(0);
    expect(toQuery(f)).toEqual({ hidden: false, order: "for_you" });
    expect(fromParams(new URLSearchParams("order=best")).order).toBe("latest");
  });

  it("leave out what isn't set, and drop what is malformed", () => {
    expect(toParams(NO_FILTERS).toString()).toBe("");
    expect(isFiltered(NO_FILTERS)).toBe(false);
    const f = fromParams(new URLSearchParams("from=yesterday&to=2026-13&automation=x&unreviewed=yes&kinds=,a.b,"));
    expect(f).toEqual({ ...NO_FILTERS, kinds: ["a.b"] });
  });

  it("make the API's query, always leaving out what you hid", () => {
    expect(toQuery(NO_FILTERS)).toEqual({ hidden: false });
    const q = toQuery({
      ...NO_FILTERS,
      unreviewed: true,
      important: true,
      automation: "4",
      from: "2026-10-01",
      to: "2026-10-02",
    });
    expect(q.reviewed).toBe(false);
    expect(q.important).toBe(true);
    expect(q.where).toBe(automationWhere("4"));
    expect(JSON.parse(q.where as string).any).toHaveLength(2);
    // the whole of both days, in the viewer's own time
    expect(Date.parse(q.since as string)).toBe(new Date(2026, 9, 1).getTime() - 1);
    expect(Date.parse(q.until as string)).toBe(new Date(2026, 9, 2, 23, 59, 59, 999).getTime());
  });
});

describe("grouping", () => {
  it("collapses a run of the same actor and kind within 30 minutes into one line", () => {
    const items = [act("a", 0), act("b", 5), act("c", 30), act("d", 61), act("e", 62, "email.received"), act("f", 63)];
    const gs = group(items);
    expect(gs.map((g) => g.items.map((a) => a.id))).toEqual([["a", "b", "c"], ["d"], ["e"], ["f"]]);
    expect(groupSummary(gs[0])).toBe("Work Gmail added 3 assets");
    expect(groupSummary(gs[1])).toBe("Work Gmail added “d”");
  });

  it("reads a first mailbox sync as one line, and keeps other actors apart", () => {
    const sync = Array.from({ length: 37 }, (_, i) => act(`m${i}`, i, "email.received"));
    const other = act("x", 40, "email.received", "lens:source/2", "Home Gmail");
    const gs = group([...sync, other]);
    expect(gs).toHaveLength(2);
    expect(groupSummary(gs[0])).toBe("Work Gmail received 37 emails");
    expect(
      groupSummary({ key: "k", items: [act("1", 0, "thing.done"), act("2", 1, "thing.done")] }, "Thing done"),
    ).toBe("Work Gmail: 2 × thing done");
  });
});

describe("new items", () => {
  it("are those not shown yet, merged on top newest first without doubles", () => {
    const shown = [act("b", 10), act("a", 20)];
    const incoming = [act("c", 5), act("b", 10), act("d", 1)];
    expect(unseen(shown, incoming).map((a) => a.id)).toEqual(["c", "d"]);
    expect(mergeNew(shown, incoming).map((a) => a.id)).toEqual(["d", "c", "b", "a"]);
    expect(newest(mergeNew(shown, incoming))).toBe(act("d", 1).published);
    expect(newest([])).toBeUndefined();
  });
});

describe("kinds and objects", () => {
  it("puts each kind under its family", () => {
    expect(familyOf("email.received")).toBe("Email");
    expect(familyOf("calendar_event.added")).toBe("Calendar");
    expect(familyOf("asset.added")).toBe("Files");
    expect(familyOf("sensor.reading")).toBe("Sensors");
    expect(familyOf("chat.message")).toBe("Chat");
    expect(familyOf("automation.ran")).toBe("Automations");
    expect(familyOf("note.saved")).toBe("Notes");
    expect(familyOf("backup.finished")).toBe("System");
    const fam = kindsByFamily([
      { name: "file.added", label: "File added" },
      { name: "email.received", label: "Email received" },
      { name: "asset.added", label: "Asset added" },
    ]);
    expect(fam.map(([f, ks]) => [f, ks.map((k) => k.name)])).toEqual([
      ["Email", ["email.received"]],
      ["Files", ["asset.added", "file.added"]],
    ]);
  });

  it("links an activity to its object and lists the object's own fields", () => {
    const a = act("7", 0);
    expect(objectHref(a)).toBe("/resources/7");
    expect(objectHref({ ...a, object: { type: "Note", id: "lens:entity/9" } })).toBe("/entities/9");
    expect(objectHref({ ...a, object: { type: "Note", id: "lens:nothing/9" } })).toBeNull();
    const fields = objectFields({
      ...a,
      object: { ...a.object, media: "document", from: ["office@stmarys.org"], deep: { x: 1 }, empty: "" },
    });
    expect(fields).toEqual([
      ["media", "document"],
      ["from", "office@stmarys.org"],
    ]);
  });
});

test("a saved view keeps the filters and order as they are, and offers a name from them", () => {
  const f = fromParams(
    new URLSearchParams("kinds=email.received&ns=work&from=2026-10-01&automation=4&important=1&order=for_you"),
  );
  expect(fromViewState(toViewState(f))).toEqual(f);
  expect(toViewState(NO_FILTERS)).toMatchObject({ from_day: null, to_day: null, automation: null, order: "latest" });
  expect(fromViewState({})).toEqual(NO_FILTERS);
  expect(sameFilters(f, { ...f, order: "latest" })).toBe(false);
  expect(viewName(f, { "email.received": "Email received" })).toBe("work · Email received · Important · For you");
  expect(viewName(NO_FILTERS)).toBe("My view");
});

test("a snooze ends an hour, a day or a week from now", () => {
  const now = Date.parse("2026-10-19T09:00:00Z");
  expect(snoozeUntil("an hour", now)).toBe("2026-10-19T10:00:00.000Z");
  expect(snoozeUntil("a day", now)).toBe("2026-10-20T09:00:00.000Z");
  expect(snoozeUntil("a week", now)).toBe("2026-10-26T09:00:00.000Z");
});

test("a snooze to a time you pick starts at nine tomorrow, in the browser's own time", () => {
  const d = tomorrowMorning(new Date(2026, 9, 31, 22, 15));
  expect(localInput(d)).toBe("2026-11-01T09:00");
  expect(localInput(new Date(2026, 0, 5, 7, 3))).toBe("2026-01-05T07:03");
});
