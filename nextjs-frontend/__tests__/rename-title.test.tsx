import { act, fireEvent, render, screen } from "@testing-library/react";
import "@testing-library/jest-dom";

import { RenameTitle } from "@/components/ui/rename-title";
import { TooltipProvider } from "@/components/ui/tooltip";

const show = (onRename?: (name: string) => Promise<unknown>) =>
  render(
    <TooltipProvider>
      <RenameTitle name="Transcribe to report" what="pipeline" onRename={onRename} />
    </TooltipProvider>,
  );

describe("a title renamed in place", () => {
  it("opens with the name selected, and Enter saves the new one", async () => {
    const rename = jest.fn(() => Promise.resolve());
    show(rename);
    expect(screen.getByRole("heading", { name: "Transcribe to report" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Rename pipeline" }));
    const field = screen.getByRole("textbox", { name: "Pipeline name" });
    expect(field).toHaveFocus();
    expect([(field as HTMLInputElement).selectionStart, (field as HTMLInputElement).selectionEnd]).toEqual([0, 20]);
    fireEvent.change(field, { target: { value: " Podcasts " } });
    await act(async () => {
      fireEvent.submit(field);
    });
    expect(rename).toHaveBeenCalledWith("Podcasts");
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
  });

  it("keeps the old name on Escape, or when nothing changed", async () => {
    const rename = jest.fn(() => Promise.resolve());
    show(rename);
    fireEvent.click(screen.getByRole("button", { name: "Rename pipeline" }));
    fireEvent.keyDown(screen.getByRole("textbox"), { key: "Escape" });
    expect(screen.getByRole("heading", { name: "Transcribe to report" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Rename pipeline" }));
    await act(async () => {
      fireEvent.submit(screen.getByRole("textbox"));
    });
    expect(rename).not.toHaveBeenCalled();
  });

  it("is a plain title for people who can't rename it", () => {
    show();
    expect(screen.queryByRole("button", { name: "Rename pipeline" })).not.toBeInTheDocument();
  });
});
