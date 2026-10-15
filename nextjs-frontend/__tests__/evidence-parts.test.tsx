import { fireEvent, render, screen } from "@testing-library/react";
import "@testing-library/jest-dom";

import { DEFAULT_PARTS, EVIDENCE_PARTS, EvidencePartsDialog } from "@/components/evidence/parts-dialog";
import { TooltipProvider } from "@/components/ui/tooltip";

function open(onDownload: (parts: string[]) => void) {
  return render(
    <TooltipProvider>
      <EvidencePartsDialog open onOpenChange={() => undefined} onDownload={onDownload} what="“Weekly sync”" />
    </TooltipProvider>,
  );
}

describe("the evidence PDF's parts", () => {
  beforeEach(() => localStorage.clear());

  it("start with everything Lens found, and Download is focused so Enter downloads", () => {
    const download = jest.fn();
    open(download);
    expect(DEFAULT_PARTS).not.toContain("notes");
    expect(DEFAULT_PARTS).toHaveLength(EVIDENCE_PARTS.length - 1);
    expect(screen.getByRole("checkbox", { name: "Emotion and sounds" })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: "Notes, highlights and comments" })).not.toBeChecked();
    const button = screen.getByRole("button", { name: "Download PDF" });
    expect(button).toHaveFocus();
    fireEvent.click(button);
    expect(download).toHaveBeenCalledWith(DEFAULT_PARTS);
  });

  it("send the parts picked, in their order, and start from them next time", () => {
    const download = jest.fn();
    const { unmount } = open(download);
    fireEvent.click(screen.getByRole("checkbox", { name: "Everything" })); // some picked: picks them all
    fireEvent.click(screen.getByRole("checkbox", { name: "Everything" })); // all picked: picks none
    expect(screen.getByRole("button", { name: "Download PDF" })).toHaveAttribute("aria-disabled", "true");
    fireEvent.click(screen.getByRole("checkbox", { name: "Notes, highlights and comments" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "Its own file" }));
    fireEvent.click(screen.getByRole("button", { name: "Download PDF" }));
    expect(download).toHaveBeenCalledWith(["file", "notes"]);
    unmount();

    open(download);
    expect(screen.getByRole("checkbox", { name: "Notes, highlights and comments" })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: "Summary" })).not.toBeChecked();
  });
});
