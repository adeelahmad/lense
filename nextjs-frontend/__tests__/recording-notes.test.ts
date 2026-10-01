import type { Note } from "@/app/openapi-client/types.gen";
import {
  QUOTE_MAX,
  deleteQuestion,
  draftFromSelection,
  groupNotes,
  momentLabel,
  newNoteBody,
  noteCounts,
  noteMeta,
  notesAt,
  writer,
} from "@/components/recording/notes-model";

const note = (over: Partial<Note>): Note => ({
  id: 1,
  recording: 5,
  text: "Check the claim",
  t0: null,
  t1: null,
  quote: null,
  shared: false,
  created_by: "vi@x.io",
  created_by_name: null,
  mine: true,
  can_delete: true,
  ...over,
});

describe("notes", () => {
  it("drafts a note from a transcript selection", () => {
    expect(draftFromSelection(4200.4, 9100, "  the capsid\n model   beat ")).toEqual({
      t0: 4200,
      t1: 9100,
      quote: "the capsid model beat",
    });
    // an end before the start is the start; a long quote is cut with an ellipsis
    const d = draftFromSelection(5000, 1000, "word ".repeat(400));
    expect(d.t1).toBe(5000);
    expect(d.quote).toHaveLength(QUOTE_MAX);
    expect(d.quote.endsWith("…")).toBe(true);
    expect(draftFromSelection(-3, -1, "x").t0).toBe(0);
  });

  it("names the moment a note is about", () => {
    expect(momentLabel(note({}))).toBeNull();
    expect(momentLabel(note({ t0: 83000, t1: 83900 }))).toBe("1:23");
    expect(momentLabel(note({ t0: 83000, t1: 91000 }))).toBe("1:23–1:31");
    expect(momentLabel(note({ t0: 3723000 }))).toBe("1:02:03");
  });

  it("says who wrote it and who sees it", () => {
    expect(writer(note({}))).toBe("You");
    expect(writer(note({ mine: false, created_by_name: "Ana" }))).toBe("Ana");
    expect(writer(note({ mine: false, created_by: "ed@x.io" }))).toBe("ed@x.io");
    expect(writer(note({ mine: false, created_by: null }))).toBe("Someone");
    expect(noteMeta(note({}))).toBe("Only you");
    expect(noteMeta(note({ shared: true }))).toBe("You · shared");
    expect(noteMeta(note({ shared: true, mine: false, created_by: "ed@x.io" }))).toBe("ed@x.io · shared");
  });

  it("asks before deleting, for everyone when it's shared", () => {
    expect(deleteQuestion(note({}))).toBe("Delete this note?");
    expect(deleteQuestion(note({ shared: true }))).toBe("Delete it for everyone who can read this recording?");
    expect(deleteQuestion(note({ shared: true, mine: false, created_by_name: "Ana" }))).toBe(
      "Delete Ana’s note for everyone?",
    );
  });

  it("groups and counts them", () => {
    const whole = note({ id: 1 });
    const at = note({ id: 2, t0: 1000, t1: 1000, mine: false, shared: true });
    const span = note({ id: 3, t0: 4000, t1: 9000 });
    expect(groupNotes([whole, at, span])).toEqual({ whole: [whole], moments: [at, span] });
    expect(noteCounts([whole, at, span])).toEqual({ mine: 2, shared: 1 });
    // a line from 5 s to 6 s has the span's note; a line from 0 to 1 s has none (the instant at 1 s starts the next)
    expect(notesAt([whole, at, span], 5000, 6000)).toEqual([span]);
    expect(notesAt([whole, at, span], 0, 1000)).toEqual([]);
    expect(notesAt([whole, at, span], 1000, 2000)).toEqual([at]);
  });

  it("builds the body for a new note", () => {
    const draft = { t0: 4000, t1: 9000, quote: "the capsid" };
    expect(newNoteBody("  Check it ", { draft, at: 12000, shared: true })).toEqual({
      text: "Check it",
      shared: true,
      t0: 4000,
      t1: 9000,
      quote: "the capsid",
    });
    expect(newNoteBody("Here", { draft: null, at: 12345.6, shared: false })).toEqual({
      text: "Here",
      shared: false,
      t0: 12346,
    });
    expect(newNoteBody("All of it", { draft: null, at: null, shared: false })).toEqual({
      text: "All of it",
      shared: false,
    });
    expect(newNoteBody("No words", { draft: { ...draft, quote: "" }, at: null, shared: false })).toEqual({
      text: "No words",
      shared: false,
      t0: 4000,
      t1: 9000,
    });
  });
});
