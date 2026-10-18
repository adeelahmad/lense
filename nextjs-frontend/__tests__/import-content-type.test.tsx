import { fireEvent, render, screen } from "@testing-library/react";
import "@testing-library/jest-dom";

import type { ContentType } from "@/app/openapi-client/types.gen";
import { baseOf, ContentTypeSelect } from "@/components/import/content-type-field";

jest.mock("next-auth/react", () => ({ useSession: () => ({ data: { accessToken: "t" } }) }));

const t = (key: string, base: ContentType["base"], label: string, description?: string): ContentType => ({
  key,
  base,
  label,
  description,
  general: key === base,
  builtin: true,
});
const TYPES = [
  t("audio", "audio", "Audio", "Any audio"),
  t("podcast", "audio", "Podcast", "An episode of a show"),
  t("interview", "audio", "Interview"),
  t("scan", "image", "Scanned page"),
  t("document", "text", "Document"),
];

describe("choosing what a file is as it's imported", () => {
  it("offers the content types of the file's base type, and none chosen means recognised from the file", () => {
    const onChange = jest.fn();
    const { rerender } = render(<ContentTypeSelect base="audio" value={null} onChange={onChange} types={TYPES} />);
    const select = screen.getByRole("combobox", { name: "What it is" });
    expect(select).toHaveValue("");
    expect(Array.from((select as HTMLSelectElement).options).map((o) => o.text)).toEqual([
      "Recognised automatically",
      "Audio",
      "Podcast",
      "Interview",
    ]);
    expect(screen.getByText("Lens tells what it is from its name and type.")).toBeInTheDocument();
    fireEvent.change(select, { target: { value: "podcast" } });
    expect(onChange).toHaveBeenCalledWith("podcast");
    rerender(<ContentTypeSelect base="audio" value="podcast" onChange={onChange} types={TYPES} />);
    expect(screen.getByText("An episode of a show: its pipeline runs.")).toBeInTheDocument();
    fireEvent.change(select, { target: { value: "" } });
    expect(onChange).toHaveBeenLastCalledWith(null);
    // one of another base type isn't offered, or shown as chosen
    rerender(<ContentTypeSelect base="image" value="podcast" onChange={onChange} types={TYPES} />);
    expect(screen.getByRole("combobox", { name: "What it is" })).toHaveValue("");
  });

  it("tells a file's base type from its kind, as the server does", () => {
    expect(["audio", "video", "image", "document", "transcript"].map((k) => baseOf(k as never))).toEqual([
      "audio",
      "video",
      "image",
      "text",
      "text",
    ]);
  });
});
