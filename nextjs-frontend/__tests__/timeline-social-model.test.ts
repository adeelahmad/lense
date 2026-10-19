import { applyReaction, commentsLabel, linked, reactedBy, sumReactions } from "@/components/timeline/social-model";

test("a reaction moves the count by one and keeps the palette's order", () => {
  const list = [{ emoji: "❤️", count: 2, mine: false }];
  const liked = applyReaction(list, "👍", true);
  expect(liked).toEqual([
    { emoji: "👍", count: 1, mine: true },
    { emoji: "❤️", count: 2, mine: false },
  ]);
  expect(applyReaction(liked, "👍", true)).toBe(liked); // giving it again changes nothing
  expect(applyReaction(liked, "👍", false)).toEqual(list);
  expect(applyReaction(applyReaction(list, "❤️", true), "❤️", false)).toEqual(list);
});

test("a line sums its items' reactions, yours when you gave it on any", () => {
  expect(
    sumReactions([
      [{ emoji: "👍", count: 2, mine: false }],
      [
        { emoji: "👍", count: 1, mine: true },
        { emoji: "🎉", count: 1, mine: false },
      ],
      [],
    ]),
  ).toEqual([
    { emoji: "👍", count: 3, mine: true, who: [] },
    { emoji: "🎉", count: 1, mine: false, who: [] },
  ]);
  expect(
    sumReactions([
      [{ emoji: "👍", count: 2, mine: false, who: ["Sam", "Vi"] }],
      [{ emoji: "👍", count: 1, mine: false, who: ["Sam"] }],
    ]),
  ).toEqual([{ emoji: "👍", count: 3, mine: false, who: ["Sam", "Vi"] }]);
  expect(commentsLabel(0)).toBe("");
  expect(commentsLabel(1)).toBe("1 comment");
  expect(commentsLabel(4)).toBe("4 comments");
});

test("a reaction's hover says who gave it: you first, then up to five names, then how many more", () => {
  expect(reactedBy({ emoji: "👍", count: 1, mine: true, who: [] })).toBe("You");
  expect(reactedBy({ emoji: "👍", count: 2, mine: true, who: ["Sam"] })).toBe("You and Sam");
  expect(reactedBy({ emoji: "👍", count: 3, mine: false, who: ["Sam", "Vi"] })).toBe("Sam, Vi and 1 other");
  expect(reactedBy({ emoji: "👍", count: 9, mine: true, who: ["A", "B", "C", "D", "E"] })).toBe(
    "You, A, B, C, D, E and 3 others",
  );
  expect(reactedBy({ emoji: "👍", count: 2, mine: false })).toBe("2 others");
});

test("the agent's reply links its addresses", () => {
  expect(linked("Done.\nDecide in Lens: Write the note\nhttps://lens.local/approvals?id=3 (soon)")).toEqual([
    { text: "Done.\nDecide in Lens: Write the note\n" },
    { text: "https://lens.local/approvals?id=3", url: "https://lens.local/approvals?id=3" },
    { text: " (soon)" },
  ]);
  expect(linked("no links here")).toEqual([{ text: "no links here" }]);
});
