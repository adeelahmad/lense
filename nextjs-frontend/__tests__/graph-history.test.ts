import { diffLines, type Diff } from "@/components/routines/history-model";

const empty: Diff = {
  entities: { added: [], removed: [], changed: [] },
  aliases: { added: [], removed: [] },
  links: { added: [], removed: [] },
  distinct: { added: [], removed: [] },
};

describe("graph history diffs", () => {
  it("lists only what changed, in words", () => {
    expect(diffLines(empty)).toEqual([]);
    const d: Diff = {
      ...empty,
      entities: {
        added: [{ id: 1, name: "Lens", type: "PRODUCT" }],
        removed: [],
        changed: [
          {
            id: 2,
            name: "Dyno",
            fields: { name: ["Dyno Therapeutics", "Dyno"], key: ["a", "b"], hidden: [null, true] },
          },
        ],
      },
      aliases: { added: [{ key: "dyno therapeutics", entity: 2, name: "Dyno" }], removed: [] },
      links: { added: [{ a: 2, b: 9, names: ["Dyno", "Dyno Therapeutics"] }], removed: [] },
    };
    expect(diffLines(d)).toEqual([
      { label: "Added", items: ["Lens (PRODUCT)"] },
      { label: "Changed", items: ["Dyno: name Dyno Therapeutics → Dyno, hidden — → true"] },
      { label: "Other names added", items: ["“dyno therapeutics” → Dyno"] },
      { label: "Linked", items: ["Dyno ↔ Dyno Therapeutics"] },
    ]);
  });
});
