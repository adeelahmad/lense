import { fireEvent, render, screen } from "@testing-library/react";
import "@testing-library/jest-dom";

import { Composer } from "@/components/chat/composer";

jest.mock("next-auth/react", () => ({ useSession: () => ({ data: { accessToken: "t" } }) }));
jest.mock("@/lib/hooks/session", () => ({ useArchive: () => ({}) }));
jest.mock("@/components/import/pending", () => ({ chooseFiles: jest.fn(() => Promise.resolve([])) }));

function box(props: Partial<Parameters<typeof Composer>[0]> = {}) {
  const onSend = jest.fn();
  const onFiles = jest.fn();
  render(
    <Composer
      value=""
      onChange={() => {}}
      onSend={onSend}
      onFiles={onFiles}
      busy={false}
      placeholder="Ask"
      {...props}
    />,
  );
  return { onSend, onFiles, input: screen.getByLabelText("Your question") };
}

describe("the chat composer", () => {
  it("sends while an answer is being written, to go next", () => {
    const { onSend, input } = box({ value: "and the budget?", busy: true });
    expect(screen.getByRole("button", { name: "Send when this answer is done" })).toBeEnabled();
    fireEvent.keyDown(input, { key: "Enter" });
    expect(onSend).toHaveBeenCalledTimes(1);
  });

  it("sends files without words, but not before they've uploaded", () => {
    const { onSend } = box({ hasFiles: true });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    expect(onSend).toHaveBeenCalled();
  });

  it("waits for files still uploading", () => {
    const { onSend, input } = box({ value: "here", hasFiles: true, uploading: true });
    expect(screen.getByRole("button", { name: "Waiting for the files to upload" })).toBeDisabled();
    fireEvent.keyDown(input, { key: "Enter" });
    expect(onSend).not.toHaveBeenCalled();
  });

  it("takes files dropped or pasted on it", () => {
    const { onFiles, input } = box();
    const f = new File(["x"], "memo.m4a");
    fireEvent.drop(input.closest("form")!, { dataTransfer: { files: [f], types: ["Files"] } });
    expect(onFiles).toHaveBeenCalledWith([f]);
    fireEvent.paste(input, { clipboardData: { files: [f] } });
    expect(onFiles).toHaveBeenCalledTimes(2);
    expect(screen.getByRole("button", { name: "Attach files" })).toBeInTheDocument();
  });
});
