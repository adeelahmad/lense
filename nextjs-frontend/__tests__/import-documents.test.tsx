import { fireEvent, render, screen } from "@testing-library/react";
import "@testing-library/jest-dom";

import { asKind, DEFAULT_LIMITS } from "@/components/import/files";
import { ImportAs } from "@/components/import/import-as";
import type { Item } from "@/components/import/use-import";

const item = (name: string, kind: Item["kind"]): Item => {
  const file = new File([new Uint8Array(8)], name);
  return {
    id: "f1",
    file,
    ...asKind(file, kind, DEFAULT_LIMITS),
    title: "Harbour report",
    mapping: "",
    mappingTouched: false,
  };
};

describe("importing a PDF", () => {
  it("is a document, uploaded as it is, unless someone chooses a transcript", () => {
    const onKind = jest.fn();
    const it = item("harbour.pdf", "document");
    expect(it.status).toBe("ready"); // nothing to read before it goes
    render(<ImportAs it={it} onKind={onKind} />);
    expect(screen.getByRole("radiogroup", { name: "Import as" })).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: "Document" })).toHaveAttribute("aria-checked", "true");
    expect(screen.getByText("Its pages, to look at and search.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("radio", { name: "Transcript" }));
    expect(onKind).toHaveBeenCalledWith("transcript");
  });

  it("is read first when it's a transcript, and only a PDF has the choice", () => {
    const pdf = item("harbour.pdf", "transcript");
    expect(pdf.status).toBe("reading");
    const { container, rerender } = render(<ImportAs it={pdf} onKind={() => {}} />);
    expect(screen.getByRole("radio", { name: "Transcript" })).toHaveAttribute("aria-checked", "true");
    expect(screen.getByText("Only its text, as a transcript without media.")).toBeInTheDocument();
    rerender(<ImportAs it={item("notes.docx", "transcript")} onKind={() => {}} />);
    expect(container).toBeEmptyDOMElement();
    expect(item("photo.png", "image").status).toBe("ready");
    expect(item("photo.png", "image").problem).toBeUndefined();
    const blocked = asKind({ name: "big.pdf", size: 60 * 1024 * 1024 }, "transcript", DEFAULT_LIMITS);
    expect(blocked).toMatchObject({ status: "blocked", problem: { code: "too-large" } });
  });
});
