import { fireEvent, render, screen } from "@testing-library/react";

import type { Segment, Turn, Word } from "@/components/recording/model";
import { TurnView } from "@/components/recording/turn";

jest.mock("next-auth/react", () => ({ useSession: () => ({ data: { accessToken: "t" } }) }));

const seg: Segment = {
  idx: 0,
  t0: 1000,
  t1: 4000,
  speaker: null,
  text: "Book a call Friday.",
  emotion: null,
  event: null,
  words: [
    [0, 4, 1000, 1300, 0.95],
    [5, 6, 1300, 1400],
    [12, 19, 2000, 2900, 0.62],
  ] as Word[],
};
const turn = { key: 0, speaker: null, t0: 1000, t1: 4000, segs: [0] } as unknown as Turn;

function view(unsureBelow: number | null, onSeek = jest.fn()) {
  return render(
    <TurnView
      turn={turn}
      segments={[seg]}
      speaker={null}
      active={-1}
      hits={new Map()}
      currentHit={null}
      entityNames={new Map()}
      unsure={null}
      unsureBelow={unsureBelow}
      editing={false}
      editTarget={null}
      onSeek={onSeek}
      onEdit={jest.fn()}
    />,
  );
}

describe("words transcription wasn't sure of", () => {
  it("are underlined with how sure, and a click still plays the line", () => {
    const onSeek = jest.fn();
    view(0.7, onSeek);
    const word = screen.getByTitle("Transcription wasn't sure of this word (62%)");
    expect(word.textContent).toBe("Friday.");
    expect(word.className).toContain("decoration-dotted");
    fireEvent.click(word);
    expect(onSeek).toHaveBeenCalledWith(1000);
  });

  it("are not marked when it's off", () => {
    view(null);
    expect(screen.queryByTitle(/wasn't sure/)).toBeNull();
    expect(screen.getByText("Book a call Friday.")).toBeTruthy();
  });
});
