import { fireEvent, render, screen, within } from "@testing-library/react";
import "@testing-library/jest-dom";

import type { PublicRecording } from "@/app/openapi-client/types.gen";
import { DocumentBody } from "@/components/public/document-view";
import { TooltipProvider } from "@/components/ui/tooltip";

jest.mock("next-auth/react", () => ({ useSession: () => ({ data: null, status: "unauthenticated" }) }));
jest.mock("next/navigation", () => ({
  usePathname: () => "/explore/recordings/9",
  useSearchParams: () => new URLSearchParams(),
}));

const pg = (idx: number, label: string | null = null) => ({
  idx,
  width: 1545,
  height: 2000,
  image: `/api/v1/recordings/9/frames/page-000${idx + 1}.jpg?sig=x`,
  thumb: `/api/v1/recordings/9/frames/thumb-000${idx + 1}.jpg?sig=x`,
  label,
});
const REC = {
  id: 9,
  title: "Harbour report",
  view: "public",
  access: "public",
  media_kind: "document",
  open: ["media", "transcript", "index"],
  closed: [],
  media: {
    url: "/api/v1/recordings/9/media?sig=x",
    kind: "document",
    poster: null,
    envelope: null,
    pages: [pg(0), pg(1), pg(2, "iii")],
  },
  transcript: {
    speakers: [],
    segments: [
      { t0: 0, t1: 1000, s: null, text: "The harbour report", p: 0 },
      { t0: 1000, t1: 2000, s: null, text: "Ships arrived at dawn.", p: 0 },
      { t0: 2000, t1: 3000, s: null, text: "The lighthouse keeper wrote twice.", p: 2 },
    ],
    downloads: [],
  },
  chapters: [{ t0: 2000, t1: 3000, title: "The keeper" }],
} as unknown as PublicRecording;

function show(
  rec: PublicRecording = REC,
  { page = null, start = null } = {} as { page?: number | null; start?: number | null },
) {
  return render(
    <TooltipProvider>
      <DocumentBody rec={rec} signedIn={false} start={start} page={page} head={<h1>{rec.title}</h1>} aside={null} />
    </TooltipProvider>,
  );
}
const shown = () => screen.getByRole("img", { name: /^Page / });

beforeAll(() => {
  Element.prototype.scrollIntoView = jest.fn();
});

describe("a document's public page", () => {
  it("shows its pages, turns them and offers its file", () => {
    show();
    expect(shown()).toHaveAttribute("src", REC.media?.pages?.[0].image);
    expect(screen.getByText("Page 1 of 3")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Next page" }));
    expect(shown()).toHaveAttribute("alt", "Page 2");
    const thumbs = screen.getByRole("navigation", { name: "Pages to choose" });
    fireEvent.click(within(thumbs).getByRole("button", { name: "Page iii" }));
    expect(shown()).toHaveAttribute("alt", "Page iii");
    expect(screen.getByRole("button", { name: "Next page" })).toBeDisabled();
    expect(screen.getByRole("link", { name: "Download the PDF" })).toHaveAttribute("href", REC.media?.url);
  });

  it("reads its text page by page; a page's name, a match or a section turns to it", () => {
    show();
    expect(screen.getByRole("region", { name: "Page 1" })).toHaveTextContent("Ships arrived at dawn.");
    expect(screen.getByRole("region", { name: "Page iii" })).toHaveTextContent("The lighthouse keeper wrote twice.");
    const text = screen.getByRole("region", { name: "Text" });
    fireEvent.click(within(text).getByRole("button", { name: "Show page iii" }));
    expect(shown()).toHaveAttribute("alt", "Page iii");
    fireEvent.click(screen.getByRole("button", { name: "Previous page" }));
    expect(shown()).toHaveAttribute("alt", "Page 2");

    const find = screen.getByRole("searchbox", { name: "Find in the text" });
    fireEvent.change(find, { target: { value: "ships" } });
    fireEvent.keyDown(find, { key: "Enter" });
    expect(screen.getByText("1 of 1")).toBeInTheDocument();
    expect(shown()).toHaveAttribute("alt", "Page 1");
    expect(screen.getByText("Ships", { selector: "mark" })).toBeInTheDocument();

    const sections = screen.getByRole("region", { name: "Sections" });
    fireEvent.click(within(sections).getByRole("button", { name: "Show page iii" }));
    expect(shown()).toHaveAttribute("alt", "Page iii");
  });

  it("opens on the page asked for, or the page of the moment asked for", () => {
    const { unmount } = show(REC, { page: 2 });
    expect(shown()).toHaveAttribute("alt", "Page 2");
    unmount();
    show(REC, { start: 2 });
    expect(shown()).toHaveAttribute("alt", "Page iii");
  });

  it("puts a lock where its pages or text aren't open to the visitor", () => {
    show({
      ...REC,
      media: null,
      transcript: null,
      chapters: null,
      closed: ["media", "transcript", "index"],
    } as unknown as PublicRecording);
    expect(screen.getByText("The pages aren’t open to everyone")).toBeInTheDocument();
    expect(screen.getByText("The text isn’t open to everyone")).toBeInTheDocument();
    expect(screen.getByText("The sections aren’t open to everyone")).toBeInTheDocument();
    expect(screen.queryByRole("img")).toBeNull();
  });

  it("shows an image as one picture, named by its title, with no pages to turn", () => {
    show({
      ...REC,
      media_kind: "image",
      media: { ...REC.media, kind: "image", pages: [pg(0)] },
      transcript: { ...REC.transcript, segments: [{ t0: 0, t1: 1000, s: null, text: "Galway harbour", p: 0 }] },
      chapters: [],
    } as unknown as PublicRecording);
    expect(screen.getByRole("img", { name: "Harbour report" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Next page" })).toBeNull();
    expect(screen.getByRole("link", { name: "Download the image" })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "The text" })).toHaveTextContent("Galway harbour");
  });
});
