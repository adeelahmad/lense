import type { Approval } from "@/app/openapi-client/types.gen";
import { navFor } from "@/components/app-shell/nav-config";
import {
  badgeCount,
  changedParams,
  DAY,
  draftOf,
  effectLabel,
  expiresLine,
  groupApprovals,
  outcomeLine,
  paramLines,
} from "@/components/approvals/model";
import { buildAttention } from "@/components/home/attention";

const NOW = Date.parse("2026-10-16T09:00:00Z");

function approval(id: number, over: Partial<Approval> = {}): Approval {
  return {
    id,
    tool: "action",
    status: "pending",
    summary: `Post number ${id}`,
    origin: { automation: 4, run: 51, node: `n${id}` },
    automation: "School emails",
    intent: { action: "notify", params: { text: "Trip on Friday", times: 2 }, effect: "out" },
    ...over,
  };
}

describe("the approvals inbox's model", () => {
  it("groups each automation's together, and the assistant's by conversation, newest group first", () => {
    const chat = approval(9, { origin: null, intent: null, tool: "run_template", chat: 3, automation: null });
    const groups = groupApprovals([
      approval(12),
      chat,
      approval(8, { origin: { automation: 5 }, automation: null }),
      approval(7),
    ]);
    expect(groups.map((g) => [g.title, g.items.map((a) => a.id)])).toEqual([
      ["School emails", [12, 7]],
      ["The assistant, in a conversation", [9]],
      ["Automation 5", [8]],
    ]);
    expect(groups[1].chat).toBe(3);
    expect(groups[0].automation).toBe(4);
  });

  it("says what an effect means and when it expires", () => {
    expect(effectLabel("out")).toBe("Reaches outside Lens");
    expect(effectLabel("lens")).toBe("Changes something in Lens");
    expect(effectLabel(undefined)).toBeNull();
    expect(expiresLine(new Date(NOW + 6 * DAY).toISOString(), NOW)).toBe("Expires in 6 days");
    expect(expiresLine(new Date(NOW + DAY + 1000).toISOString(), NOW)).toBe("Expires in 1 day");
    expect(expiresLine(new Date(NOW + 3600_000).toISOString(), NOW)).toBe("Expires within a day");
    expect(expiresLine(new Date(NOW - 1).toISOString(), NOW)).toBe("Expired");
    expect(expiresLine(null, NOW)).toBeNull();
  });

  it("shows params in a line each, and an edit keeps their types and leaves out what's emptied", () => {
    const params = { text: "Trip on Friday", times: 2, rooms: ["family"], note: "" };
    expect(paramLines(params)).toEqual([
      { key: "text", value: "Trip on Friday" },
      { key: "times", value: "2" },
      { key: "rooms", value: '["family"]' },
    ]);
    const draft = draftOf(params);
    expect(draft).toEqual({ text: "Trip on Friday", times: "2", rooms: '["family"]', note: "" });
    expect(changedParams({ ...draft, times: "3", text: "" }, params)).toEqual({ times: 3, text: null });
    expect(changedParams(draft, params)).toEqual({}); // nothing changed, nothing sent
    expect(() => changedParams({ ...draft, rooms: "[family" }, params)).toThrow("rooms isn’t valid");
  });

  it("says what became of a decided one", () => {
    expect(outcomeLine({ status: "done", summary: "Post it", decided_by: "sam@x.io" })).toBe(
      "Approved by sam@x.io: Post it",
    );
    expect(outcomeLine({ status: "failed", summary: "Post it", error: "the room is gone" })).toBe(
      "Approved, but it failed: Post it (the room is gone)",
    );
    expect(outcomeLine({ status: "declined", summary: "Post it" })).toBe("Declined: Post it");
    expect(outcomeLine({ status: "expired", summary: "Post it" })).toBe("Expired with no pick: Post it");
  });

  it("counts in the badge and the nav, and Home says what waits", () => {
    expect(badgeCount(0)).toBeNull();
    expect(badgeCount(3)).toBe("3");
    expect(badgeCount(140)).toBe("99+");
    for (const admin of [true, false]) {
      const item = navFor(admin).find((i) => i.href === "/approvals");
      expect(item).toMatchObject({ label: "Approvals", countKey: "approvals", whileCounted: true, group: "Automate" });
    }
    const home = (waiting: number, automations: number) =>
      buildAttention({ latestJobs: new Map(), recent: [], nsById: new Map(), approvals: { waiting, automations } });
    expect(home(0, 0)).toEqual([]);
    expect(home(2, 2)).toEqual([
      expect.objectContaining({
        key: "approvals",
        kind: "gate",
        title: "2 actions wait for your approval",
        meta: "from automations",
        action: { label: "Review", do: { type: "link", href: "/approvals" } },
      }),
    ]);
    expect(home(1, 0)[0]).toMatchObject({ title: "1 action waits for your approval", meta: "from the assistant" });
    expect(home(3, 1)[0].meta).toBe("1 from automations, 2 from the assistant");
  });
});
