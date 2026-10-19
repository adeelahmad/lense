import { dayValue, ended, firstDay, isoToDay, untilLine, untilToIso } from "@/components/access/until";

describe("grants that end", () => {
  it("sends the end of the day picked, and nothing for an empty field", () => {
    const iso = untilToIso("2026-11-03");
    expect(iso).not.toBeNull();
    const end = new Date(iso as string);
    expect([end.getFullYear(), end.getMonth(), end.getDate(), end.getHours(), end.getMinutes()]).toEqual([
      2026, 10, 3, 23, 59,
    ]);
    expect(untilToIso("")).toBeNull();
    expect(untilToIso("  ")).toBeNull();
    expect(untilToIso("soon")).toBeNull();
  });

  it("shows a stored end back as the day it falls on", () => {
    expect(isoToDay(untilToIso("2026-11-03"))).toBe("2026-11-03");
    expect(isoToDay(null)).toBe("");
    expect(isoToDay("not a date")).toBe("");
    expect(dayValue(new Date(2026, 0, 5))).toBe("2026-01-05");
    expect(firstDay(new Date(2026, 9, 18, 9))).toBe("2026-10-18");
  });

  it("says until when, or that it ended", () => {
    const now = new Date(2026, 9, 18, 12);
    const later = untilToIso("2026-11-03") as string;
    const before = untilToIso("2026-10-01") as string;
    expect(untilLine({ expires_at: later }, now)).toBe("Until 3 Nov 2026");
    expect(untilLine({ expires_at: before }, now)).toBe("Ended 1 Oct 2026");
    // the API's word counts, and so does an end that came after the list was loaded
    expect(untilLine({ expires_at: later, expired: true }, now)).toBe("Ended 3 Nov 2026");
    expect(ended({ expires_at: later }, new Date(2026, 11, 1))).toBe(true);
    expect(untilLine({ expires_at: null }, now)).toBe("");
    expect(untilLine({}, now)).toBe("");
    expect(ended({}, now)).toBe(false);
  });
});
