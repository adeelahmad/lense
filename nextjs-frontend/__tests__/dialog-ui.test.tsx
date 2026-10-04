import { fireEvent, render, screen } from "@testing-library/react";
import "@testing-library/jest-dom";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { TooltipProvider } from "@/components/ui/tooltip";

function Rename({ onSave, ready = true }: { onSave: (v: string) => void; ready?: boolean }) {
  const [name, setName] = useState("");
  return (
    <TooltipProvider>
      <Dialog
        open
        onOpenChange={() => undefined}
        title="Rename"
        actions={
          <>
            <Button variant="ghost">Cancel</Button>
            <Button variant="primary" disabled={!ready} onClick={() => onSave(name)}>
              Save
            </Button>
          </>
        }
      >
        <input aria-label="Name" value={name} onChange={(e) => setName(e.target.value)} />
        <textarea aria-label="Notes" />
      </Dialog>
    </TooltipProvider>
  );
}

describe("dialogs", () => {
  it("open on their first field, and Enter there saves", () => {
    const save = jest.fn();
    render(<Rename onSave={save} />);
    const name = screen.getByLabelText("Name");
    expect(name).toHaveFocus();
    fireEvent.change(name, { target: { value: "Weekly call" } });
    fireEvent.keyDown(name, { key: "Enter" });
    expect(save).toHaveBeenCalledWith("Weekly call");
  });

  it("leave Enter alone in text areas and while the main action can't run", () => {
    const save = jest.fn();
    const { unmount } = render(<Rename onSave={save} />);
    fireEvent.keyDown(screen.getByLabelText("Notes"), { key: "Enter" });
    fireEvent.keyDown(screen.getByLabelText("Name"), { key: "Enter", shiftKey: true });
    expect(save).not.toHaveBeenCalled();
    unmount();
    render(<Rename onSave={save} ready={false} />);
    fireEvent.keyDown(screen.getByLabelText("Name"), { key: "Enter" });
    expect(save).not.toHaveBeenCalled();
  });

  it("keep the close button first when there is nothing to type", () => {
    render(
      <Dialog open onOpenChange={() => undefined} title="Sure?" actions={<Button variant="danger">Delete</Button>} />,
    );
    expect(screen.getByLabelText("Close")).toHaveFocus();
  });
});
