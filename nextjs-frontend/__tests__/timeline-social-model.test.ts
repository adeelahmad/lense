import { applyReaction, commentsLabel, linked, sumReactions } from "@/components/timeline/social-model";

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
    { emoji: "👍", count: 3, mine: true },
    { emoji: "🎉", count: 1, mine: false },
  ]);
  expect(commentsLabel(0)).toBe("");
  expect(commentsLabel(1)).toBe("1 comment");
  expect(commentsLabel(4)).toBe("4 comments");
});

test("the agent's reply links its addresses", () => {
  expect(linked("Done.\nDecide in Lens: Write the note\nhttps://lens.local/approvals?id=3 (soon)")).toEqual([
    { text: "Done.\nDecide in Lens: Write the note\n" },
    { text: "https://lens.local/approvals?id=3", url: "https://lens.local/approvals?id=3" },
    { text: " (soon)" },
  ]);
  expect(linked("no links here")).toEqual([{ text: "no links here" }]);
});
